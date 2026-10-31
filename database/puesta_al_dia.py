"""
PWOS -- Puesta al dia automatica de jobs (2026-09-24).

Los jobs de n8n corren SOLO si la PC esta prendida a esa hora, y n8n no
repone los disparos perdidos: un dia con la PC apagada o suspendida deja un
hueco (diagnostico completo en ESTADO_PWOS.md, 2026-09-23/24). Este script
lo recupera desde la fuente. Lo dispara un workflow horario de n8n via
`POST /api/puesta-al-dia`; es IDEMPOTENTE:

  1. DETECCION (solo lectura, sin red): por tabla, que dias faltan entre lo
     ya cargado y hoy. Si no falta nada -> NO-OP REAL: no abre la base en
     escritura, no hace backup, no toca la red.
  2. RELLENO (solo lo que falto): una tarea por tabla, cada una en su
     transaccion, con una sola copia de seguridad por corrida (integrity_check).
     Todo lo que se hace queda en `puesta_al_dia_log` (que, cuantas filas, de
     que fuente).
  3. ALERTAS: un hueco que sigue sin poder rellenarse pasados
     UMBRAL_DIAS_HABILES_ALERTA dias habiles, o un dia que ya no se puede
     reconstruir fielmente, se devuelve en `alertas` (el workflow de n8n manda
     el mail). Cada alerta se emite como maximo 1 vez cada 24 h.

Tareas (fuente de cada una):
  precios_rv     acciones/CEDEARs           yfinance `<ticker>.BA` (Close, auto_adjust=False)
  precios_fx     USD MEP / CCL              api.argentinadatos.com (dias corridos)
  precios_rf     renta fija                 Rava `prices/historico` (por ticker, con pausa)
  riesgo_pais    macro_historico            argentinadatos (serie completa, INSERT OR IGNORE)
  tbill_3m       macro_historico            yfinance ^IRX
  macro          benchmarks_macro + resto   reejecuta cargar_benchmarks_macro si vencio (>26 h)
  mensuales      economia real / zpindex    reejecuta el cargador si paso el dia 5 y no corrio
  snapshot_bonos mercado_renta_fija_historico  ver `tarea_snapshot_bonos`

Feriados: si la fuente responde con fechas POSTERIORES pero no con la
pedida, esa fecha se marca 'sin_rueda' en el log y no se vuelve a consultar.

Snapshot de bonos -- que se puede reconstruir (medido, ver ESTADO_PWOS.md):
  - dia SIN mercado (fin de semana/feriado): copia exacta del ultimo dia
    habil (el domingo 09-20 == viernes 09-18 en 903/903 tickers); origen
    'copia_dia_sin_rueda'.
  - dia HABIL perdido, si todavia no abrio la rueda siguiente (11:00 ART): se
    refresca `mercado_renta_fija_rava` en vivo (con el mercado cerrado es el
    estado de cierre) y se toma el snapshot con la fecha de ese dia.
  - dia HABIL perdido y ya abierta la rueda siguiente: NO recuperable (Rava
    historico solo trae el precio; TIR/duration/paridad y carry/BPV/breakeven
    no se reconstruyen fielmente) -> log 'no_recuperable' + alerta. No se inventa.

Uso (parado en database/):
    python puesta_al_dia.py [--dry-run] [--db <ruta>] [--ahora 2026-09-24T09:00]
La ultima linea de stdout es un JSON con el resultado (lo lee el endpoint).
"""
import argparse
import importlib.util
import json
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import configuracion

ART = ZoneInfo("America/Argentina/Buenos_Aires")
DB_PATH = configuracion.ruta_db()

UMBRAL_DIAS_HABILES_ALERTA = 2     # N: dias habiles sin poder rellenarse -> alerta
LOOKBACK_DIAS = 45                 # solo se miran huecos de los ultimos 45 dias
HORA_DATO_HOY = 19                 # desde las 19 ART se espera ya el dato de HOY
HORA_APERTURA = 11                 # BYMA abre a las 11:00 ART
MACRO_VENCIDO_HORAS = 26
DIVIDENDOS_VENCIDO_HORAS = 24  # cronograma de dividendos (yfinance): anuncios nuevos salen cada tanto, 1 vez por dia alcanza
REINTENTO_ERROR_HORAS = 6
MEP_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"
CCL_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/contadoconliqui"
RAVA_BASE = "https://mercado.rava.com"
RAVA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Accept": "application/json"}
RAVA_DELAY_SEG = 1.5
MIN_FRACCION_SIN_DATO = 0.5        # una fecha esta "sin resolver" si falta en >= 50% de la serie


# ------------------------------------------------------------------ utilidades

def dias_habiles(desde: date, hasta: date) -> list[date]:
    out, d = [], desde
    while d <= hasta:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def dias_corridos(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]


def fin_esperado(ahora: datetime) -> date:
    """Ultimo dia para el que ya se espera dato de mercado."""
    return ahora.date() if ahora.hour >= HORA_DATO_HOY else ahora.date() - timedelta(days=1)


def proximo_dia_habil(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def cargar_modulo_por_ruta(nombre: str, archivo: str):
    """Los backfills fechados tienen guiones en el nombre: no se pueden `import`ar."""
    spec = importlib.util.spec_from_file_location(nombre, Path(__file__).parent / archivo)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Contexto:
    def __init__(self, con_ro: sqlite3.Connection, ahora: datetime, db_path: Path, dry_run: bool):
        self.ro, self.ahora, self.db_path, self.dry_run = con_ro, ahora, db_path, dry_run
        self.hoy = ahora.date()
        self.fin = fin_esperado(ahora)
        self.inicio = self.hoy - timedelta(days=LOOKBACK_DIAS)
        self.ejecucion = ahora.isoformat(timespec="seconds")
        self.rw: sqlite3.Connection | None = None
        self.backup_hecho = False
        self.log_mem: list[dict] = []
        self.rellenos: list[dict] = []
        self.errores: list[dict] = []
        self.pendientes: dict[str, list[str]] = {}

    # --- log (en memoria + tabla si hay conexion de escritura)
    def log(self, tarea, accion, fecha=None, filas=0, fuente=None, detalle=None):
        reg = {"tarea": tarea, "accion": accion, "fecha": fecha, "filas": filas, "fuente": fuente, "detalle": detalle}
        self.log_mem.append(reg)
        if not self.dry_run:
            self._abrir().execute(
                "INSERT INTO puesta_al_dia_log (ejecucion, tarea, accion, fecha_dato, filas, fuente, detalle) VALUES (?,?,?,?,?,?,?);",
                (self.ejecucion, tarea, accion, fecha, filas, fuente, detalle))

    def marcas(self, tarea: str, accion: str = "sin_rueda") -> set[str]:
        return {r[0] for r in self.ro.execute(
            "SELECT DISTINCT fecha_dato FROM puesta_al_dia_log WHERE tarea=? AND accion=? AND fecha_dato IS NOT NULL;", (tarea, accion))}

    def _abrir(self) -> sqlite3.Connection:
        if self.rw is None:
            self.rw = sqlite3.connect(self.db_path, isolation_level=None)
            self.rw.execute("PRAGMA foreign_keys = ON;")
        return self.rw

    def escritura(self) -> sqlite3.Connection:
        """Conexion para escribir DATOS: hace UNA copia de seguridad por corrida, la primera vez que
        hace falta. (El log y las marcas de feriado usan `_abrir()`: escribir solo en la bitacora no
        justifica un backup de toda la base.)"""
        if not self.backup_hecho and not self.dry_run:
            backup = self.db_path.parent / "backups" / f"pwos_backup_pre-puesta-al-dia_{self.ahora:%Y-%m-%d_%H%M%S}.db"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.db_path, backup)
            chk = sqlite3.connect(backup)
            ok = chk.execute("PRAGMA integrity_check;").fetchone()[0]
            chk.close()
            if ok != "ok":
                raise RuntimeError(f"backup corrupto ({ok})")
            self.backup_hecho = True
        return self._abrir()


def _insertar_precios(ctx: Contexto, filas: list[tuple[int, str, float, str, str]]) -> int:
    if not filas or ctx.dry_run:
        return len(filas)
    con = ctx.escritura()
    n = 0
    con.execute("BEGIN;")
    try:
        for aid, f, p, mon, fuente in filas:
            n += con.execute("INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?,?,?,?,?);",
                             (aid, f, p, mon, fuente)).rowcount
        con.execute("COMMIT;")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    return n


def _pendientes_por_fecha(faltan: dict[str, set[str]], total_series: int) -> list[str]:
    """Fechas 'sin resolver': faltan en >= MIN_FRACCION_SIN_DATO de las series."""
    por_fecha: dict[str, int] = {}
    for _s, fechas in faltan.items():
        for f in fechas:
            por_fecha[f] = por_fecha.get(f, 0) + 1
    return sorted(f for f, n in por_fecha.items() if total_series and n / total_series >= MIN_FRACCION_SIN_DATO)


# ------------------------------------------------------------------ tareas de precios

def _faltantes_precios(ctx: Contexto, activos: list[tuple[int, str]], calendario, tarea_marcas: str) -> dict[tuple[int, str], set[str]]:
    """{(activo_id, ticker): {fechas faltantes}} sobre `calendario` (dias esperados)."""
    marcas = ctx.marcas(tarea_marcas) | ctx.marcas(tarea_marcas, "no_recuperable")
    out = {}
    for aid, tk in activos:
        primera = ctx.ro.execute("SELECT MIN(fecha) FROM precios WHERE activo_id=?;", (aid,)).fetchone()[0]
        existentes = {r[0] for r in ctx.ro.execute("SELECT fecha FROM precios WHERE activo_id=? AND fecha>=?;", (aid, ctx.inicio.isoformat()))}
        esperados = {d.isoformat() for d in calendario if d.isoformat() >= (primera or "0000") and d.isoformat() not in marcas}
        falta = esperados - existentes
        if falta:
            out[(aid, tk)] = falta
    return out


def _marcar_sin_rueda(ctx: Contexto, tarea: str, fechas_pedidas: set[str], fechas_con_dato: set[str]) -> set[str]:
    """Feriado = la fuente no tiene NINGUNA serie con dato esa fecha pero si fechas posteriores."""
    if not fechas_con_dato:
        return set()
    ultimo = max(fechas_con_dato)
    feriados = {f for f in fechas_pedidas if f not in fechas_con_dato and f < ultimo}
    for f in sorted(feriados):
        ctx.log(tarea, "sin_rueda", f, 0, None, "la fuente no tiene datos ese dia pero si posteriores (feriado/sin rueda)")
    return feriados


def tarea_precios_rv(ctx: Contexto, solo_detectar: bool = False):
    import yfinance as yf
    activos = ctx.ro.execute("""
        SELECT a.id, a.ticker FROM activos a WHERE a.tipo IN ('accion','cedear')
          AND EXISTS (SELECT 1 FROM precios p WHERE p.activo_id=a.id AND p.fecha >= ?);""",
        ((ctx.hoy - timedelta(days=14)).isoformat(),)).fetchall()
    faltan = _faltantes_precios(ctx, activos, dias_habiles(ctx.inicio, ctx.fin), "precios_rv")
    if solo_detectar or not faltan:
        return {"faltantes": sum(len(v) for v in faltan.values()), "tickers": len(faltan)}
    con_dato: set[str] = set()
    a_insertar, sin_cobertura = [], []
    for (aid, tk), fechas in sorted(faltan.items(), key=lambda x: x[0][1]):
        ini = (date.fromisoformat(min(fechas)) - timedelta(days=7)).isoformat()
        fin = (date.fromisoformat(max(fechas)) + timedelta(days=1)).isoformat()
        hist = yf.Ticker(f"{tk.replace('.', '-')}.BA").history(start=ini, end=fin, auto_adjust=False)
        if hist.empty:
            sin_cobertura.append(tk)
            continue
        serie = {ts.strftime("%Y-%m-%d"): float(v) for ts, v in hist["Close"].dropna().items()}
        con_dato |= set(serie)
        for f in fechas:
            if f in serie:
                a_insertar.append((aid, f, serie[f], "ARS", "yfinance_historico"))
    n = _insertar_precios(ctx, a_insertar)
    todas = set().union(*faltan.values())
    feriados = _marcar_sin_rueda(ctx, "precios_rv", todas, con_dato)
    if n:
        ctx.log("precios_rv", "relleno", None, n, "yfinance_historico",
                f"{len({a[0] for a in a_insertar})} tickers, fechas {min(a[1] for a in a_insertar)}..{max(a[1] for a in a_insertar)}")
        ctx.rellenos.append({"tarea": "precios_rv", "filas": n, "fuente": "yfinance .BA"})
    if sin_cobertura:
        ctx.log("precios_rv", "no_recuperable", None, 0, "yfinance", f"sin cobertura: {', '.join(sin_cobertura)}")
    insertadas_por_serie = {}
    for aid, f, *_ in a_insertar:
        insertadas_por_serie.setdefault(aid, set()).add(f)
    restantes = {k: (v - insertadas_por_serie.get(k[0], set()) - feriados) for k, v in faltan.items() if k[1] not in sin_cobertura}
    ctx.pendientes["precios_rv"] = _pendientes_por_fecha({k: v for k, v in restantes.items() if v}, len(restantes))
    return {"insertadas": n, "feriados": sorted(feriados)}


def tarea_precios_fx(ctx: Contexto, solo_detectar: bool = False):
    activos = ctx.ro.execute("SELECT id, ticker FROM activos WHERE ticker IN ('USD_MEP','USD_CCL');").fetchall()
    faltan = _faltantes_precios(ctx, activos, dias_corridos(ctx.inicio, ctx.fin), "precios_fx")
    if solo_detectar or not faltan:
        return {"faltantes": sum(len(v) for v in faltan.values())}
    urls = {"USD_MEP": MEP_URL, "USD_CCL": CCL_URL}
    a_insertar, restantes = [], {}
    for (aid, tk), fechas in faltan.items():
        r = requests.get(urls[tk], timeout=30, headers={"User-Agent": "pwos-puesta-al-dia"})
        r.raise_for_status()
        serie = {f["fecha"]: float(f["venta"]) for f in r.json() if f.get("venta")}
        for f in fechas:
            if f in serie:
                a_insertar.append((aid, f, serie[f], "ARS", "argentinadatos_historico_diario"))
        restantes[(aid, tk)] = {f for f in fechas if f not in serie}
    n = _insertar_precios(ctx, a_insertar)
    if n:
        ctx.log("precios_fx", "relleno", None, n, "argentinadatos_historico_diario", "MEP/CCL")
        ctx.rellenos.append({"tarea": "precios_fx", "filas": n, "fuente": "argentinadatos"})
    ctx.pendientes["precios_fx"] = _pendientes_por_fecha({k: v for k, v in restantes.items() if v}, len(restantes))
    return {"insertadas": n}


def tarea_precios_rf(ctx: Contexto, solo_detectar: bool = False):
    import backfill_precios_rava as rava
    candidatos = {(aid, tk): tipo for aid, tk, tipo in rava.tickers_a_backfillear(ctx.ro)}
    activos = [(aid, tk) for (aid, tk) in candidatos
               if ctx.ro.execute("SELECT 1 FROM precios WHERE activo_id=? AND fecha>=? LIMIT 1;",
                                 (aid, (ctx.hoy - timedelta(days=14)).isoformat())).fetchone()
               and rava.moneda_precio_rava(tk, candidatos[(aid, tk)]) is not None]
    faltan = _faltantes_precios(ctx, activos, dias_habiles(ctx.inicio, ctx.fin), "precios_rf")
    if solo_detectar or not faltan:
        return {"faltantes": sum(len(v) for v in faltan.values()), "tickers": len(faltan)}
    con_dato: set[str] = set()
    a_insertar, sin_cobertura = [], []
    for i, ((aid, tk), fechas) in enumerate(sorted(faltan.items(), key=lambda x: x[0][1])):
        dias = (ctx.hoy - date.fromisoformat(min(fechas))).days + 10
        r = requests.get(f"{RAVA_BASE}/api/prices/historico/arg/{tk}", params={"dias": dias}, headers=RAVA_HEADERS, timeout=20)
        if r.status_code == 429:
            raise RuntimeError(f"Rava respondio 429 (rate limit) pidiendo {tk}: se frena, se reintenta en la proxima corrida")
        if r.status_code == 404:
            sin_cobertura.append(tk)
            continue
        r.raise_for_status()
        serie = {d["fecha"][:10]: d["precio"] for d in r.json().get("datos", [])}
        if not serie:
            sin_cobertura.append(tk)
            continue
        con_dato |= set(serie)
        moneda = rava.moneda_precio_rava(tk, candidatos[(aid, tk)])
        for f in fechas:
            if f in serie:
                a_insertar.append((aid, f, serie[f], moneda, "rava_historico"))
        time.sleep(RAVA_DELAY_SEG)
    n = _insertar_precios(ctx, a_insertar)
    todas = set().union(*faltan.values())
    feriados = _marcar_sin_rueda(ctx, "precios_rf", todas, con_dato)
    if n:
        ctx.log("precios_rf", "relleno", None, n, "rava_historico",
                f"{len({a[0] for a in a_insertar})} tickers, fechas {min(a[1] for a in a_insertar)}..{max(a[1] for a in a_insertar)}")
        ctx.rellenos.append({"tarea": "precios_rf", "filas": n, "fuente": "Rava historico"})
    if sin_cobertura:
        ctx.log("precios_rf", "no_recuperable", None, 0, "rava_historico", f"sin cobertura: {', '.join(sin_cobertura)}")
    ins = {}
    for aid, f, *_ in a_insertar:
        ins.setdefault(aid, set()).add(f)
    restantes = {k: (v - ins.get(k[0], set()) - feriados) for k, v in faltan.items() if k[1] not in sin_cobertura}
    ctx.pendientes["precios_rf"] = _pendientes_por_fecha({k: v for k, v in restantes.items() if v}, len(restantes))
    return {"insertadas": n, "feriados": sorted(feriados)}


# ------------------------------------------------------------------ macro

def _ultima_fecha_macro(ctx: Contexto, clave: str) -> str | None:
    return ctx.ro.execute("SELECT MAX(fecha) FROM macro_historico WHERE clave=?;", (clave,)).fetchone()[0]


def _serie_macro_faltante(ctx: Contexto, clave: str) -> bool:
    ult = _ultima_fecha_macro(ctx, clave)
    if ult is None:
        return False
    esperado = [d for d in dias_habiles(date.fromisoformat(ult) + timedelta(days=1), ctx.fin) if d.isoformat() not in ctx.marcas(f"macro_{clave}") | ctx.marcas(f"macro_{clave}", "no_recuperable")]
    return bool(esperado)


def tarea_riesgo_pais(ctx: Contexto, solo_detectar: bool = False):
    if not _serie_macro_faltante(ctx, "riesgo_pais"):
        return {"faltantes": 0}
    if solo_detectar:
        return {"faltantes": 1}
    cb = cargar_modulo_por_ruta("cbm", "cargar_benchmarks_macro.py")
    r = requests.get(cb.RIESGO_PAIS_HIST_URL, headers=cb._UA, timeout=30)
    r.raise_for_status()
    ult = _ultima_fecha_macro(ctx, "riesgo_pais")
    filas = [("riesgo_pais", f["fecha"], float(f["valor"])) for f in r.json() if f.get("valor") is not None and f["fecha"] > ult]
    return _insertar_macro(ctx, "riesgo_pais", filas, "argentinadatos (EMBI+)", ult)


def tarea_tbill(ctx: Contexto, solo_detectar: bool = False):
    if not _serie_macro_faltante(ctx, "tbill_3m"):
        return {"faltantes": 0}
    if solo_detectar:
        return {"faltantes": 1}
    import yfinance as yf
    ult = _ultima_fecha_macro(ctx, "tbill_3m")
    hist = yf.Ticker("^IRX").history(start=ult, auto_adjust=False)["Close"].dropna()
    filas = [("tbill_3m", ts.strftime("%Y-%m-%d"), round(float(v), 4)) for ts, v in hist.items() if ts.strftime("%Y-%m-%d") > ult]
    return _insertar_macro(ctx, "tbill_3m", filas, "yfinance ^IRX", ult)


def _insertar_macro(ctx: Contexto, clave: str, filas: list[tuple], fuente: str, ultima_previa: str):
    n = len(filas)
    if filas and not ctx.dry_run:
        con = ctx.escritura()
        con.execute("BEGIN;")
        try:
            n = sum(con.execute("INSERT OR IGNORE INTO macro_historico (clave, fecha, valor) VALUES (?,?,?);", f).rowcount for f in filas)
            con.execute("COMMIT;")
        except Exception:
            con.execute("ROLLBACK;")
            raise
    if n:
        ctx.log(f"macro_{clave}", "relleno", None, n, fuente, f"desde {filas[0][1]} hasta {filas[-1][1]}")
        ctx.rellenos.append({"tarea": clave, "filas": n, "fuente": fuente})
    fechas = {f[1] for f in filas}
    esperados = [d.isoformat() for d in dias_habiles(date.fromisoformat(ultima_previa) + timedelta(days=1), ctx.fin)]
    if fechas:
        feriados = {d for d in esperados if d not in fechas and d < max(fechas)}
        for d in sorted(feriados):
            ctx.log(f"macro_{clave}", "sin_rueda", d, 0, None, "la fuente no tiene dato ese dia pero si posteriores")
    else:
        feriados = set()
    ctx.pendientes[clave] = sorted(d for d in esperados if d not in fechas and d not in feriados)
    return {"insertadas": n}


def _antiguedad_horas(ts: str | None, ahora: datetime) -> float | None:
    if not ts:
        return None
    t = datetime.fromisoformat(ts.replace(" ", "T"))
    # `actualizado_en` se escribe con datetime.now() del proceso que corrio (UTC en el contenedor,
    # hora local en la PC): el error maximo es de 3 h, que absorbe MACRO_VENCIDO_HORAS.
    return (ahora.replace(tzinfo=None) - t).total_seconds() / 3600


def _ultimo_error_reciente(ctx: Contexto, tarea: str) -> bool:
    r = ctx.ro.execute("SELECT MAX(creado_en) FROM puesta_al_dia_log WHERE tarea=? AND accion='error';", (tarea,)).fetchone()[0]
    return bool(r) and (datetime.utcnow() - datetime.fromisoformat(r)).total_seconds() < REINTENTO_ERROR_HORAS * 3600  # creado_en = UTC


def _reejecutar(ctx: Contexto, tarea: str, modulo: str, motivo: str, solo_detectar: bool):
    if solo_detectar:
        return {"vencido": True}
    if _ultimo_error_reciente(ctx, tarea):
        return {"vencido": True, "omitido": f"error reciente (< {REINTENTO_ERROR_HORAS} h), se reintenta mas tarde"}
    ctx.escritura()   # backup previo de esta corrida (los cargadores hacen ademas el suyo)
    if ctx.rw is not None:
        ctx.rw.close()
        ctx.rw = None
    try:
        mod = cargar_modulo_por_ruta(f"job_{tarea}", modulo)
        if not ctx.dry_run:
            mod.cargar(ctx.db_path)
    except BaseException as e:      # incluye SystemExit de los cargadores
        raise RuntimeError(f"{modulo}: {type(e).__name__}: {e}") from e
    ctx.log(tarea, "reejecutado", None, 0, modulo, motivo)
    ctx.rellenos.append({"tarea": tarea, "filas": None, "fuente": f"reejecucion de {modulo}"})
    return {"reejecutado": True}


def tarea_macro(ctx: Contexto, solo_detectar: bool = False):
    ts = ctx.ro.execute("SELECT MAX(actualizado_en) FROM benchmarks_macro WHERE clave IN ('fed','riesgo_pais_ar','ust_10y_pct');").fetchone()[0]
    edad = _antiguedad_horas(ts, ctx.ahora)
    vencido = edad is None or (edad > MACRO_VENCIDO_HORAS and ctx.ahora.hour >= 8)
    if not vencido:
        return {"vencido": False}
    return _reejecutar(ctx, "macro", "cargar_benchmarks_macro.py", f"benchmarks_macro con {edad:.0f} h de antiguedad (job de las 7:00 no corrio)", solo_detectar)


def _mensual_vencido(ctx: Contexto, claves: tuple[str, ...]) -> bool:
    if ctx.hoy.day < 5 or ctx.ahora.hour < 7:
        return False
    ts = ctx.ro.execute(f"SELECT MIN(actualizado_en) FROM benchmarks_macro WHERE clave IN ({','.join('?' * len(claves))});", claves).fetchone()[0]
    return ts is None or ts[:10] < ctx.hoy.replace(day=5).isoformat()


def tarea_mensuales(ctx: Contexto, solo_detectar: bool = False):
    out = {}
    grupos = [("economia_real", "cargar_indicadores_economia_real.py",
               ("patentamientos_interanual_pct", "construccion_interanual_pct", "empleo_privado_interanual_pct", "mora_hogares_pct", "industria_interanual_pct")),
              ("zpindex", "cargar_zpindex.py", ("zpindex_caba_venta_usd_m2", "zpindex_cordoba_venta_usd_m2", "zpindex_rosario_venta_usd_m2"))]
    for tarea, modulo, claves in grupos:
        if _mensual_vencido(ctx, claves):
            try:
                out[tarea] = _reejecutar(ctx, tarea, modulo, "cargador mensual (dia 5) no corrio", solo_detectar)
            except Exception as e:
                ctx.errores.append({"tarea": tarea, "error": str(e)})
                ctx.log(tarea, "error", None, 0, modulo, str(e)[:500])
    return out


def tarea_dividendos(ctx: Contexto, solo_detectar: bool = False):
    """Cronograma de dividendos por accion (yfinance, schema v2.46): se
    rehace si tiene mas de DIVIDENDOS_VENCIDO_HORAS. Sin la tabla (base
    anterior a v2.46) no hace nada."""
    if ctx.ro.execute("SELECT 1 FROM sqlite_master WHERE name='dividendos_proyectados';").fetchone() is None:
        return {"vencido": False}
    ts = ctx.ro.execute("SELECT MAX(actualizado_en) FROM dividendos_proyectados;").fetchone()[0]
    edad = _antiguedad_horas(ts, ctx.ahora)
    if edad is not None and (edad <= DIVIDENDOS_VENCIDO_HORAS or ctx.ahora.hour < 8):
        return {"vencido": False}
    motivo = "sin cargar" if edad is None else f"{edad:.0f} h de antiguedad"
    return _reejecutar(ctx, "dividendos", "cargar_dividendos_proyectados.py", f"dividendos_proyectados {motivo}", solo_detectar)


# ------------------------------------------------------------------ snapshot de bonos

def _es_dia_sin_mercado(ctx: Contexto, d: date) -> bool:
    return d.weekday() >= 5 or d.isoformat() in ctx.marcas("precios_rv") or d.isoformat() in ctx.marcas("precios_rf")


def _snapshot_faltantes(ctx: Contexto) -> list[date]:
    primera = ctx.ro.execute("SELECT MIN(fecha) FROM mercado_renta_fija_historico;").fetchone()[0]
    if not primera:
        return []
    existentes = {r[0] for r in ctx.ro.execute("SELECT DISTINCT fecha FROM mercado_renta_fija_historico WHERE fecha>=?;", (ctx.inicio.isoformat(),))}
    existentes |= ctx.marcas("snapshot_bonos", "no_recuperable")     # ya declarados perdidos: no se reintentan cada hora
    fin = ctx.hoy if ctx.ahora.hour >= 18 and ctx.ahora.minute >= 15 or ctx.ahora.hour >= 19 else ctx.hoy - timedelta(days=1)
    return [d for d in dias_corridos(max(ctx.inicio, date.fromisoformat(primera)), fin) if d.isoformat() not in existentes]


def tarea_snapshot_bonos(ctx: Contexto, solo_detectar: bool = False):
    faltan = _snapshot_faltantes(ctx)
    if solo_detectar or not faltan:
        return {"faltantes": len(faltan)}
    ya_no_recuperable = ctx.marcas("snapshot_bonos", "no_recuperable")
    copias, tomados, perdidos = 0, [], []
    refrescado = False
    for d in sorted(faltan):
        ds = d.isoformat()
        if _es_dia_sin_mercado(ctx, d):
            prev = ctx.ro.execute("SELECT MAX(fecha) FROM mercado_renta_fija_historico WHERE fecha < ?;", (ds,)).fetchone()[0]
            if prev is None:
                continue
            if ctx.dry_run:
                copias += 1
                continue
            con = ctx.escritura()
            con.execute("BEGIN;")
            try:
                n = con.execute("""
                    INSERT OR IGNORE INTO mercado_renta_fija_historico
                      (ticker, fecha, categoria, moneda, tir_pct, precio_vn100, paridad_pct, duration,
                       carry_pct, bpv_usd, breakeven_pb, en_cartera, origen)
                    SELECT ticker, ?, categoria, moneda, tir_pct, precio_vn100, paridad_pct, duration,
                           carry_pct, bpv_usd, breakeven_pb, en_cartera, 'copia_dia_sin_rueda'
                    FROM mercado_renta_fija_historico WHERE fecha = ?;""", (ds, prev)).rowcount
                con.execute("COMMIT;")
            except Exception:
                con.execute("ROLLBACK;")
                raise
            ctx.log("snapshot_bonos", "copia", ds, n, "copia_dia_sin_rueda", f"copia del snapshot del {prev} (mercado cerrado: no cambia)")
            ctx.rellenos.append({"tarea": "snapshot_bonos", "filas": n, "fuente": f"copia del {prev} ({ds} sin mercado)"})
            copias += 1
        else:
            # dia HABIL: solo es fiel si aun no abrio la rueda siguiente
            apertura_siguiente = datetime.combine(proximo_dia_habil(d), datetime.min.time(), tzinfo=ART).replace(hour=HORA_APERTURA)
            if ctx.ahora < apertura_siguiente and d >= ctx.hoy - timedelta(days=4) and ds not in ya_no_recuperable:
                if ctx.dry_run:
                    tomados.append(ds)
                    continue
                if not refrescado:
                    mod_rava = cargar_modulo_por_ruta("job_mercado_rava", "cargar_mercado_renta_fija_rava.py")
                    ctx.escritura()
                    ctx.rw.close(); ctx.rw = None
                    mod_rava.cargar(ctx.db_path)
                    refrescado = True
                mod_h = cargar_modulo_por_ruta("job_hist_bonos", "cargar_mercado_renta_fija_historico.py")
                mod_h.cargar(ctx.db_path, fecha=ds)
                ctx.escritura()
                ctx.log("snapshot_bonos", "relleno", ds, 0, "Rava en vivo (mercado cerrado = estado de cierre)",
                        "snapshot tomado despues del horario habitual, antes de la apertura siguiente")
                ctx.rellenos.append({"tarea": "snapshot_bonos", "filas": None, "fuente": f"snapshot tardio del {ds}"})
                tomados.append(ds)
            else:
                perdidos.append(ds)
                if ds not in ya_no_recuperable:
                    ctx.log("snapshot_bonos", "no_recuperable", ds, 0, None,
                            "dia habil cuyo snapshot se perdio y ya abrio la rueda siguiente: Rava historico solo trae precio, "
                            "no se reconstruyen TIR/duration/paridad ni carry/BPV/breakeven sin inventar")
    ctx.pendientes["snapshot_bonos"] = sorted(set(perdidos) | (ya_no_recuperable & {d.isoformat() for d in faltan}))
    return {"copias": copias, "tomados": tomados, "no_recuperables": perdidos}


# ------------------------------------------------------------------ orquestacion

TAREAS = [
    ("precios_rv", tarea_precios_rv), ("precios_fx", tarea_precios_fx), ("precios_rf", tarea_precios_rf),
    ("riesgo_pais", tarea_riesgo_pais), ("tbill_3m", tarea_tbill), ("macro", tarea_macro),
    ("mensuales", tarea_mensuales), ("snapshot_bonos", tarea_snapshot_bonos),
    ("dividendos", tarea_dividendos),
]


def _hay_algo(det: dict) -> bool:
    if not isinstance(det, dict):
        return False
    return any(bool(v) for k, v in det.items() if k in ("faltantes", "vencido", "economia_real", "zpindex"))


def _alertas(ctx: Contexto) -> list[dict]:
    """Huecos que siguen sin rellenarse pasados N dias habiles + dias no recuperables. Cada alerta, 1 vez cada 24 h."""
    candidatas = []
    for tarea, fechas in ctx.pendientes.items():
        fechas = [f for f in fechas]
        if not fechas:
            continue
        antiguos = [f for f in fechas if len(dias_habiles(date.fromisoformat(f), ctx.fin)) - 1 >= UMBRAL_DIAS_HABILES_ALERTA]
        if tarea == "snapshot_bonos":
            antiguos = fechas          # un dia habil no recuperable ya es alerta por si mismo
        if antiguos:
            candidatas.append({"tarea": tarea, "fechas": antiguos,
                               "mensaje": f"{tarea}: {len(antiguos)} dia(s) sin poder rellenarse ({', '.join(antiguos[:6])}"
                                          f"{'...' if len(antiguos) > 6 else ''}); umbral {UMBRAL_DIAS_HABILES_ALERTA} dias habiles"})
    for e in ctx.errores:
        candidatas.append({"tarea": e["tarea"], "fechas": [], "mensaje": f"{e['tarea']}: fallo la puesta al dia ({e['error'][:200]})"})
    emitir = []
    for a in candidatas:
        clave = f"{a['tarea']}|{','.join(a['fechas'])}|{a['mensaje'][:60] if not a['fechas'] else ''}"
        ya = ctx.ro.execute("SELECT 1 FROM puesta_al_dia_log WHERE tarea=? AND accion='alerta' AND detalle=? "
                            "AND creado_en >= datetime('now','-24 hours') LIMIT 1;", (a["tarea"], clave)).fetchone()
        if not ya:
            a["clave"] = clave
            emitir.append(a)
    return emitir


def ejecutar(db_path: Path = DB_PATH, dry_run: bool = False, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(ART)
    ro = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    ctx = Contexto(ro, ahora, Path(db_path), dry_run)
    inicio = time.time()

    # ---- 1. DETECCION (solo lectura, sin red)
    deteccion = {}
    for nombre, fn in TAREAS:
        try:
            deteccion[nombre] = fn(ctx, solo_detectar=True)
        except Exception as e:
            deteccion[nombre] = {"error_deteccion": str(e)}
    hay_trabajo = any(_hay_algo(d) for d in deteccion.values())
    if not hay_trabajo:
        ro.close()
        return {"ok": True, "noop": True, "ahora": ahora.isoformat(timespec="seconds"), "fin_esperado": ctx.fin.isoformat(),
                "detectado": deteccion, "rellenos": [], "alertas": [], "errores": [], "segundos": round(time.time() - inicio, 1)}

    # ---- 2. RELLENO
    resultados = {}
    try:
        for nombre, fn in TAREAS:
            if not _hay_algo(deteccion.get(nombre, {})):
                continue
            try:
                resultados[nombre] = fn(ctx)
            except Exception as e:
                ctx.errores.append({"tarea": nombre, "error": f"{type(e).__name__}: {e}"})
                if ctx.rw is not None and ctx.rw.in_transaction:
                    ctx.rw.execute("ROLLBACK;")
                ctx.log(nombre, "error", None, 0, None, f"{type(e).__name__}: {e}"[:500])
        alertas = _alertas(ctx)
        for a in alertas:
            ctx.log(a["tarea"], "alerta", None, 0, None, a["clave"])
        # Un hueco de precios/macro que ya supero el umbral sin poder rellenarse se declara no recuperable
        # (la alerta ya salio): se deja de reintentar la red cada hora. El snapshot lo hace su propia tarea.
        marca_de = {"riesgo_pais": "macro_riesgo_pais", "tbill_3m": "macro_tbill_3m"}
        for tarea, fechas in ctx.pendientes.items():
            if tarea == "snapshot_bonos":
                continue
            for f in fechas:
                if len(dias_habiles(date.fromisoformat(f), ctx.fin)) - 1 >= UMBRAL_DIAS_HABILES_ALERTA:
                    ctx.log(marca_de.get(tarea, tarea), "no_recuperable", f, 0, None,
                            f"sin resolver tras {UMBRAL_DIAS_HABILES_ALERTA} dias habiles: se deja de reintentar")
    finally:
        if ctx.rw is not None:
            ctx.rw.close()
        ro.close()
    return {"ok": not ctx.errores, "noop": False, "dry_run": dry_run, "ahora": ahora.isoformat(timespec="seconds"),
            "fin_esperado": ctx.fin.isoformat(), "detectado": deteccion, "resultados": resultados, "rellenos": ctx.rellenos,
            "pendientes": ctx.pendientes, "alertas": alertas, "errores": ctx.errores, "backup": ctx.backup_hecho,
            "log": ctx.log_mem, "segundos": round(time.time() - inicio, 1)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="detecta y simula: no escribe ni hace backup")
    ap.add_argument("--ahora", help="ISO, solo para pruebas (hora argentina)")
    args = ap.parse_args()
    ahora = datetime.fromisoformat(args.ahora).replace(tzinfo=ART) if args.ahora else None
    resultado = ejecutar(args.db, args.dry_run, ahora)
    print(json.dumps(resultado, ensure_ascii=False, default=str))
