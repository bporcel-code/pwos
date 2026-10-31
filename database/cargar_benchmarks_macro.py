"""
PWOS -- Carga benchmarks_macro para el header del dashboard (2026-08-29)
y el panel macro de las solapas de Analisis (2026-09-09, ver
ESTADO_PWOS.md).

Re-ejecutable (UPSERT, no INSERT ciego), mismo patron que
cargar_factores_riesgo.py: correr esto de nuevo (misma cadencia que el
resto del refresco de precios) actualiza el registro existente. Tolerante
a fallos por benchmark -- si una fuente no responde, se loguea y se sigue
con el resto (no aborta toda la carga por un solo benchmark caido).

Fuentes (todas sin API key):
- Inflacion AR interanual: api.argentinadatos.com/.../inflacionInteranual
  -- YoY ya calculado por la fuente, ultimo mes publicado tal cual.
- Inflacion AR mensual: api.argentinadatos.com/.../inflacion -- ultimo
  dato mensual publicado (INDEC).
- Devaluacion: api.argentinadatos.com/.../dolares/bolsa (USD MEP, campo
  `venta`, ADR-001) -- YoY calculado aca (fecha habil mas cercana a-o-
  antes de hace 365 dias, nunca interpolado).
- S&P 500 interanual: yfinance ^GSPC, mismo criterio de fecha.
- REM (BCRA): xlsx historico oficial (historico-relevamiento-expectativas
  -mercado.xlsx, hoja "Indicadores Principales"). Se toma la mediana del
  ultimo relevamiento para inflacion (prox. 12m / anio actual / anio
  siguiente), TAMAR TNA prox. 12m y tipo de cambio $/USD. Titular =
  inflacion prox. 12m; el resto va en detalle_json.
- CPI EEUU interanual: BLS public API v1 (serie CUUR0000SA0, CPI-U NSA),
  YoY calculado aca (ultimo mes vs. mismo mes del anio anterior).
- Fed: EFFR de la NY Fed (markets.newyorkfed.org) como titular +
  probabilidades de la proxima reunion FOMC via cme-fedwatch
  (best-effort: si CME no publica settlements ese dia, queda solo la
  fecha de la proxima reunion + el rango objetivo, sin distribucion).
- Riesgo pais Argentina (EMBI+, JP Morgan), 2026-09-11: republicado por
  api.argentinadatos.com/.../riesgo-pais/ultimo -- mismo proveedor que
  ya usa inflacion/MEP, serie diaria desde 1999, sin API key. Investigado
  primero (el usuario pidio evaluar Ambito/Rava/IAMC/Cronista o un proxy
  propio via TIR Bonares/Globales vs. Treasury comparable) -- se
  descarto el proxy: esta fuente da el EMBI+ REAL, no una aproximacion.
  OJO: `valor_pct` para esta clave viene en PUNTOS BASICOS (pb), no en
  %, pese al nombre generico de la columna (reusada tal cual para las 7
  claves de esta tabla) -- el front lo muestra con sufijo "pb", nunca
  "%", ver app.js::renderMacroPanel.
- UST 10Y / concentracion S&P 500 / EM vs. S&P 500 / Merval (Epica 15
  Parte A, 2026-09-15): 4 indicadores globales nuevos,
  todos yfinance, mismo patron que S&P 500 interanual (sin YoY -- son
  NIVEL/RATIO del ultimo cierre, no variacion). `valor_pct` vuelve a
  guardar lo que sea el "numero titular" de cada uno (mismo criterio
  que riesgo_pais en pb): UST 10Y en %, concentracion S&P 500 en %
  (ratio RSP/SPY x100), EM vs S&P 500 como ratio crudo (sin x100, el
  nombre de campo dice "ratio" no "pct"), Merval en nivel de indice.
  Usables como indicador de umbral de tesis (ver tesis_umbrales.py)
  desde esta misma sesion. Epica 21 (2026-09-29, cambio de decision del
  usuario): ahora SI entran a `macro_historico`, con backfill de yfinance
  (cargar_indicadores_macro.py --backfill).

Historial (Epica 21): cada `upsert_benchmark` tambien escribe la serie en
`macro_historico` si la clave esta mapeada en `indicadores_catalogo`
(`clave_benchmark`) -- ver indicadores_macro.upsert_serie.
"""
import io
import json
import math
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf

import indicadores_macro as im
import configuracion

# En el contenedor pwos-api, database/pwos.db es el bind mount a V: y
# tiene datos. En la PC del usuario es un stub de 0 bytes (base real en
# VeraCrypt) -> se usa V: directo. Mismo criterio que
# cargar_mercado_renta_fija_rava.py.
DB_PATH = configuracion.ruta_db()

INFLACION_INTERANUAL_URL = "https://api.argentinadatos.com/v1/finanzas/indices/inflacionInteranual"
INFLACION_MENSUAL_URL = "https://api.argentinadatos.com/v1/finanzas/indices/inflacion"
MEP_HIST_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"
REM_XLSX_URL = ("https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/"
                "informes/historico-relevamiento-expectativas-mercado.xlsx")
BLS_CPI_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_CPI_SERIE = "CUUR0000SA0"  # CPI-U, US city average, all items, NOT seasonally adjusted
NYFED_RATES_URL = "https://markets.newyorkfed.org/api/rates/all/latest.json"
# Busqueda por fechas (modo demo: la EFFR vigente a la fecha de corte).
NYFED_EFFR_BUSQUEDA_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json"
RIESGO_PAIS_URL = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais/ultimo"
# Serie completa (sin "/ultimo"), 2026-09-12: usada solo por
# backfill_macro_historico.py -- el consumo regular de este script
# (obtener_riesgo_pais) sigue pidiendo nada mas que el ultimo valor.
RIESGO_PAIS_HIST_URL = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"
_UA = {"User-Agent": "Mozilla/5.0 (PWOS benchmarks_macro fetch)"}


def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-benchmarks-macro_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


def _fecha_mas_cercana_a_o_antes(serie: dict[str, float], objetivo: date) -> str | None:
    """`serie`: {fecha_iso: valor}. Devuelve la fecha (clave de `serie`)
    mas cercana a `objetivo`, buscando hacia atras dia por dia -- nunca
    hacia adelante (no se usa un dato "del futuro" respecto del punto de
    comparacion que se esta buscando) ni se inventa un valor si no hay
    dato para ninguna fecha cercana."""
    for delta in range(0, 10):
        candidata = (objetivo - timedelta(days=delta)).isoformat()
        if candidata in serie:
            return candidata
    return None


def obtener_inflacion_interanual() -> dict | None:
    r = requests.get(INFLACION_INTERANUAL_URL, timeout=20)
    r.raise_for_status()
    datos = r.json()
    if not datos:
        return None
    ultimo = max(datos, key=lambda d: d["fecha"])
    return {"valor_pct": ultimo["valor"], "fecha_dato": ultimo["fecha"], "fuente": "argentinadatos_inflacion_interanual"}


def obtener_devaluacion_interanual() -> dict | None:
    r = requests.get(MEP_HIST_URL, timeout=20)
    r.raise_for_status()
    filas = r.json()
    serie = {f["fecha"]: f["venta"] for f in filas if f.get("venta") is not None}
    if not serie:
        return None
    fecha_hoy = max(serie.keys())
    objetivo_hace_1_anio = date.fromisoformat(fecha_hoy) - timedelta(days=365)
    fecha_hace_1_anio = _fecha_mas_cercana_a_o_antes(serie, objetivo_hace_1_anio)
    if fecha_hace_1_anio is None:
        return None
    valor_pct = (serie[fecha_hoy] / serie[fecha_hace_1_anio] - 1) * 100
    return {
        "valor_pct": valor_pct, "fecha_dato": fecha_hoy,
        "fuente": f"argentinadatos_dolares_bolsa (vs {fecha_hace_1_anio})",
    }


def obtener_sp500_interanual() -> dict | None:
    hist = yf.Ticker("^GSPC").history(period="2y", auto_adjust=False)
    if hist.empty:
        return None
    serie = {ts.date().isoformat(): float(close) for ts, close in hist["Close"].items()}
    fecha_hoy = max(serie.keys())
    objetivo_hace_1_anio = date.fromisoformat(fecha_hoy) - timedelta(days=365)
    fecha_hace_1_anio = _fecha_mas_cercana_a_o_antes(serie, objetivo_hace_1_anio)
    if fecha_hace_1_anio is None:
        return None
    valor_pct = (serie[fecha_hoy] / serie[fecha_hace_1_anio] - 1) * 100
    return {
        "valor_pct": valor_pct, "fecha_dato": fecha_hoy,
        "fuente": f"yfinance ^GSPC (vs {fecha_hace_1_anio})",
    }


def obtener_ust_10y() -> dict | None:
    """Rendimiento del Tesoro EEUU a 10 anios (^TNX). El pedido original
    decia que yfinance publica ^TNX x10 (convencion historica del
    ticker) -- verificado 2026-09-15 contra 2 anios de historico
    (`period="2y"`): el `Close` de yfinance YA es el yield real (rango
    3.62%-5.01% en los ultimos 2 anios, consistente con la realidad,
    sin factor de por medio) -- yfinance debe haber cambiado la
    convencion en algun momento. Se usa el valor tal cual, SIN dividir
    por 10 (dividir daria ~0.5%, un numero sin sentido para un
    rendimiento de bono del Tesoro)."""
    hist = yf.Ticker("^TNX").history(period="5d", auto_adjust=False)
    if hist.empty:
        return None
    fecha = hist.index[-1].date().isoformat()
    valor_pct = float(hist["Close"].iloc[-1])
    return {"valor_pct": valor_pct, "fecha_dato": fecha, "fuente": "yfinance ^TNX"}


def obtener_tbill_3m() -> dict | None:
    """Rendimiento del T-Bill a 13 semanas (^IRX) -- tasa libre de riesgo
    de la frontera eficiente de Renta Variable (Epica 19, Fase 5,
    2026-09-23). Escala verificada igual que ^TNX: el `Close` de yfinance
    ya es el yield en % (3.5%-4.6% en los ultimos 2 anios; 0.115% en
    abril 2020 y 5.28% en agosto 2023, coincide con la realidad), se usa
    tal cual, SIN factor. Anualizado, en %."""
    hist = yf.Ticker("^IRX").history(period="5d", auto_adjust=False)
    if hist.empty:
        return None
    fecha = hist.index[-1].date().isoformat()
    valor_pct = float(hist["Close"].iloc[-1])
    return {"valor_pct": valor_pct, "fecha_dato": fecha, "fuente": "yfinance ^IRX (T-Bill 13 semanas)"}


def _ratio_cierre_yfinance(ticker_a: str, ticker_b: str) -> tuple[float, str] | None:
    """Ratio de cierre MAS RECIENTE entre 2 tickers de yfinance, en la
    fecha mas reciente que TIENE dato para ambos (interseccion, no se
    asume que las 2 series publican exactamente el mismo ultimo dia)."""
    hist_a = yf.Ticker(ticker_a).history(period="5d", auto_adjust=False)
    hist_b = yf.Ticker(ticker_b).history(period="5d", auto_adjust=False)
    if hist_a.empty or hist_b.empty:
        return None
    serie_a = {ts.date().isoformat(): float(c) for ts, c in hist_a["Close"].items()}
    serie_b = {ts.date().isoformat(): float(c) for ts, c in hist_b["Close"].items()}
    fechas_comunes = sorted(set(serie_a) & set(serie_b))
    if not fechas_comunes:
        return None
    fecha = fechas_comunes[-1]
    return serie_a[fecha] / serie_b[fecha], fecha


def obtener_sp500_concentracion() -> dict | None:
    """Ratio de cierre RSP/SPY (equal-weight vs. cap-weight del S&P
    500) -- cuanto mas alto, menos concentrado el indice en las
    empresas mas grandes. Expresado en % (x100), acorde al nombre de
    la clave (`sp500_concentracion_pct`)."""
    r = _ratio_cierre_yfinance("RSP", "SPY")
    if r is None:
        return None
    ratio, fecha = r
    return {"valor_pct": ratio * 100, "fecha_dato": fecha, "fuente": "yfinance RSP/SPY x100"}


def obtener_em_vs_sp500() -> dict | None:
    """Ratio de cierre EEM/SPY (emergentes vs. S&P 500) -- ratio CRUDO,
    sin x100 (la clave es `em_vs_sp500_ratio`, no `_pct`)."""
    r = _ratio_cierre_yfinance("EEM", "SPY")
    if r is None:
        return None
    ratio, fecha = r
    return {"valor_pct": ratio, "fecha_dato": fecha, "fuente": "yfinance EEM/SPY"}


def obtener_merval() -> dict | None:
    """Nivel del indice Merval (^MERV, yfinance) -- valor absoluto, no
    variacion. Mismo criterio que riesgo_pais: `valor_pct` guarda el
    numero titular aunque no sea un porcentaje."""
    hist = yf.Ticker("^MERV").history(period="5d", auto_adjust=False)
    if hist.empty:
        return None
    fecha = hist.index[-1].date().isoformat()
    valor = float(hist["Close"].iloc[-1])
    return {"valor_pct": valor, "fecha_dato": fecha, "fuente": "yfinance ^MERV"}


def obtener_inflacion_mensual() -> dict | None:
    r = requests.get(INFLACION_MENSUAL_URL, timeout=20)
    r.raise_for_status()
    datos = r.json()
    if not datos:
        return None
    ultimo = max(datos, key=lambda d: d["fecha"])
    return {"valor_pct": ultimo["valor"], "fecha_dato": ultimo["fecha"],
            "fuente": "argentinadatos_inflacion_mensual (INDEC)"}


# ---------------------------------------------------------------- REM (BCRA)
def _rem_mediana(rows, header_row_idx, col) -> float | None:
    """La mediana de un indicador esta en la fila SIGUIENTE a su header."""
    fila_med = rows[header_row_idx + 1]
    val = fila_med[col] if col < len(fila_med) else None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _rem_norm(s: str) -> str:
    return (s or "").lower().translate(str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU"))


def _rem_extraer_columna(rows, col: int) -> dict | None:
    """Extrae los indicadores de UN relevamiento (columna `col`) de la
    grilla ya cargada del xlsx del REM. Factorizado de `obtener_rem_bcra`
    (2026-09-12, Epica 12 Parte 4) para poder reusarlo tambien sobre
    TODAS las columnas historicas (`obtener_rem_historico`), no solo la
    ultima -- misma logica exacta, cero cambio de comportamiento para
    el caso de 1 sola columna."""
    fechas = rows[1]
    fecha_survey = fechas[col]
    if fecha_survey is None:
        return None
    fecha_dato = fecha_survey.date().isoformat() if hasattr(fecha_survey, "date") else str(fecha_survey)[:10]

    objetivos = {
        "inf_prox12m": lambda l: "ipc nivel general" in l and "prox. 12 meses" in l,
        "inf_anio_actual": lambda l: "ipc nivel general" in l and l.rstrip().endswith(f": {fecha_dato[:4]}"),
        "inf_anio_sig": lambda l: "ipc nivel general" in l and l.rstrip().endswith(f": {int(fecha_dato[:4]) + 1}"),
        "tamar_prox12m": lambda l: "tamar" in l and "prox. 12 meses" in l,
        "tc_prox12m": lambda l: "tipo de cambio" in l and "prox. 12 meses" in l,
        "tc_anio_actual": lambda l: "tipo de cambio" in l and l.rstrip().endswith(f": {fecha_dato[:4]}"),
    }
    encontrado: dict = {}
    for i, row in enumerate(rows):
        lab = row[0]
        if not isinstance(lab, str) or (col < len(row) and row[col] is not None):
            continue  # solo filas header (valor propio None)
        l = _rem_norm(lab)
        for clave, test in objetivos.items():
            if clave not in encontrado and test(l):
                encontrado[clave] = _rem_mediana(rows, i, col)

    if "inf_prox12m" not in encontrado or encontrado["inf_prox12m"] is None:
        return None

    return {
        "relevamiento": fecha_dato,
        "inflacion_prox12m_pct": encontrado.get("inf_prox12m"),
        "inflacion_anio_actual_pct": encontrado.get("inf_anio_actual"),
        "inflacion_anio_sig_pct": encontrado.get("inf_anio_sig"),
        "tamar_tna_prox12m_pct": encontrado.get("tamar_prox12m"),
        "tc_usd_prox12m": encontrado.get("tc_prox12m"),
        "tc_usd_anio_actual": encontrado.get("tc_anio_actual"),
        "anio_actual": int(fecha_dato[:4]),
    }


def _rem_descargar_grilla():
    import openpyxl

    r = requests.get(REM_XLSX_URL, headers=_UA, timeout=45)
    r.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    ws = wb["Indicadores Principales"]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    return rows


def obtener_rem_bcra() -> dict | None:
    rows = _rem_descargar_grilla()
    fechas = rows[1]
    col = max(i for i, v in enumerate(fechas) if v is not None)  # ultimo relevamiento
    detalle = _rem_extraer_columna(rows, col)
    if detalle is None:
        return None
    return {"valor_pct": detalle["inflacion_prox12m_pct"], "fecha_dato": detalle["relevamiento"],
            "fuente": "BCRA REM (xlsx historico, mediana)", "detalle": detalle}


def obtener_rem_historico() -> list[dict]:
    """Los 123 relevamientos historicos (2016-06 en adelante al momento
    de escribir esto) que ya trae el MISMO xlsx que `obtener_rem_bcra`
    descarga para el ultimo valor -- Epica 12 Parte 4 (2026-09-12,
    hallazgo de esta sesion: el archivo "historico" realmente tiene
    historia completa, no hacia falta ir a buscar xlsx viejos aparte).
    Devuelve 1 dict por relevamiento (mismo shape que el `detalle` de
    obtener_rem_bcra), ordenados por fecha. Usado por
    backfill_macro_historico.py para poblar `rem_inflacion_12m` y
    `rem_devaluacion_esperada_12m` con historia real, no solo el
    ultimo valor."""
    rows = _rem_descargar_grilla()
    fechas = rows[1]
    cols = [i for i, v in enumerate(fechas) if v is not None]
    resultado = []
    for col in cols:
        detalle = _rem_extraer_columna(rows, col)
        if detalle is not None:
            resultado.append(detalle)
    resultado.sort(key=lambda d: d["relevamiento"])
    return resultado


# ---------------------------------------------------------------- CPI EEUU
def obtener_us_cpi_interanual() -> dict | None:
    hoy = date.today()
    payload = {"seriesid": [BLS_CPI_SERIE], "startyear": str(hoy.year - 2), "endyear": str(hoy.year)}
    r = requests.post(BLS_CPI_URL, json=payload, timeout=25)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != "REQUEST_SUCCEEDED":
        return None
    serie = j["Results"]["series"][0]["data"]
    puntos = []
    for x in serie:
        if not x["period"].startswith("M") or x["value"] in ("", "-", "."):
            continue
        try:
            puntos.append((f"{x['year']}-{x['period'][1:]}", float(x["value"])))
        except ValueError:
            continue
    puntos.sort()
    por_mes = dict(puntos)
    if not puntos:
        return None
    fm, vm = puntos[-1]                       # "YYYY-MM"
    y, m = fm.split("-")
    f0 = f"{int(y) - 1}-{m}"                  # mismo mes, anio anterior
    v0 = por_mes.get(f0)
    if v0 is None:
        return None
    yoy = (vm / v0 - 1) * 100
    return {"valor_pct": yoy, "fecha_dato": f"{fm}-01",
            "fuente": f"BLS {BLS_CPI_SERIE} (CPI-U NSA, vs {f0})"}


# ---------------------------------------------------------------- Fed
def _rango(lower_bps: int) -> str:
    return f"{lower_bps / 100:.2f}%-{(lower_bps + 25) / 100:.2f}%"


def probabilidades_proxima_reunion(settle_map: dict[str, float], reunion: date, tasa_vigente: float,
                                   target_low_pct: float) -> dict | None:
    """Probabilidades de la PROXIMA reunion FOMC con la metodologia de CME
    FedWatch, a partir del futuro de fondos federales (30 dias) del mes de
    la reunion (implicita = 100 - precio = promedio del mes).

    Arreglo 2026-09-30 (bug A de la Epica 21): la libreria cme-fedwatch
    toma como tasa "previa" el futuro del mes ANTERIOR. Si ese mes tuvo su
    propia reunion (la del 16-09-2026), su precio es un PROMEDIO de la tasa
    de antes y de despues -- subestima la tasa vigente y obliga a una tasa
    posterior absurda (daba 4,50-5,00 % con la Fed en 3,75-4,00 %). Para la
    PROXIMA reunion la tasa previa es la vigente hoy (EFFR), que es como
    encadena CME. `settle_map` = {'OCT 26': 96.11, ...}."""
    import calendar
    clave = reunion.strftime("%b %y").upper()
    if clave not in settle_map:
        return None
    implicita = 100.0 - settle_map[clave]
    dias_mes = calendar.monthrange(reunion.year, reunion.month)[1]
    n_post = dias_mes - reunion.day + 1
    siguiente = date(reunion.year + reunion.month // 12, reunion.month % 12 + 1, 1).strftime("%b %y").upper()
    if n_post <= 3 and siguiente in settle_map:
        # Reunion a fin de mes: el mes siguiente entero refleja la tasa nueva.
        post = 100.0 - settle_map[siguiente]
    else:
        post = (implicita * dias_mes - tasa_vigente * (reunion.day - 1)) / n_post
    movimientos = (post - tasa_vigente) / 0.25
    piso = math.floor(movimientos)
    p_arriba = min(1.0, max(0.0, movimientos - piso))
    base = round(target_low_pct * 100)
    probs = {}
    for k, p in ((piso, 1.0 - p_arriba), (piso + 1, p_arriba)):
        if p > 0.001:
            probs[_rango(base + 25 * k)] = round(p * 100, 1)
    mas_probable = max(probs, key=probs.get)
    k_top = (piso if mas_probable == _rango(base + 25 * piso) else piso + 1)
    return {
        "probabilidades": probs,
        "target_actual": _rango(base),
        "tasa_implicita_post": round(post, 3),
        "sesgo": "sin cambios" if k_top == 0 else ("suba" if k_top > 0 else "recorte"),
    }


def obtener_fed() -> dict | None:
    # Modo demo: todo a la fecha de corte (configuracion.hoy()) -- EFFR vigente,
    # proxima reunion desde el corte y liquidaciones de CME de ese dia --, no al
    # dia en que se genera la demo. En modo real, el ultimo dato.
    corte = configuracion.hoy() if configuracion.es_demo() else None
    if corte:
        r = requests.get(NYFED_EFFR_BUSQUEDA_URL, headers=_UA, timeout=20,
                         params={"startDate": (corte - timedelta(days=14)).isoformat(), "endDate": corte.isoformat()})
    else:
        r = requests.get(NYFED_RATES_URL, headers=_UA, timeout=20)
    r.raise_for_status()
    tasas = [x for x in r.json().get("refRates", []) if x.get("type") == "EFFR"]
    effr = max(tasas, key=lambda x: x["effectiveDate"]) if tasas else None
    if effr is None:
        return None
    detalle: dict = {
        "effr_pct": effr["percentRate"],
        "target_low_pct": effr.get("targetRateFrom"),
        "target_high_pct": effr.get("targetRateTo"),
        "efectivo_al": effr.get("effectiveDate"),
    }

    # Proxima reunion FOMC + (best-effort) distribucion de probabilidad.
    try:
        from cme_fedwatch.fomc import get_upcoming_meetings
        prox = get_upcoming_meetings(corte)
        detalle["prox_reunion_fomc"] = prox[0].isoformat() if prox else None
    except Exception:
        detalle["prox_reunion_fomc"] = None

    # De cme-fedwatch se usan solo los precios de liquidacion de CME; la
    # cuenta es propia (ver probabilidades_proxima_reunion: bug A).
    try:
        from cme_fedwatch.api import get_settlements
        settle_map = {x["month"]: x["settle"] for x in get_settlements(corte)}
        prox = date.fromisoformat(detalle["prox_reunion_fomc"]) if detalle.get("prox_reunion_fomc") else None
        calc = (probabilidades_proxima_reunion(settle_map, prox, effr["percentRate"], effr["targetRateFrom"])
                if prox and effr.get("targetRateFrom") is not None else None)
        if calc:
            detalle["prob_reunion"] = prox.isoformat()
            detalle.update(calc)
        else:
            detalle["probabilidades"] = None
    except Exception as e:
        detalle["probabilidades"] = None
        detalle["prob_error"] = str(e)[:120]

    return {"valor_pct": effr["percentRate"], "fecha_dato": effr["effectiveDate"],
            "fuente": "NY Fed EFFR + futuros de fondos federales (CME)", "detalle": detalle}


# ------------------------------------------------------- Riesgo pais (EMBI+)
def obtener_riesgo_pais() -> dict | None:
    """Riesgo pais Argentina (EMBI+ JP Morgan), republicado por
    argentinadatos.com -- mismo proveedor que ya usa este script para
    inflacion/MEP, sin API key, serie diaria desde 1999. `valor_pct`
    viene en PUNTOS BASICOS (pb) para esta clave, no en % -- el nombre
    de columna es generico (reusado para las 7 claves de la tabla), la
    unidad real la define `clave='riesgo_pais_ar'` en el consumidor
    (ver app.js::renderMacroPanel, que le pone sufijo "pb")."""
    r = requests.get(RIESGO_PAIS_URL, timeout=20)
    r.raise_for_status()
    j = r.json()
    if j.get("valor") is None or not j.get("fecha"):
        return None
    return {"valor_pct": j["valor"], "fecha_dato": j["fecha"],
            "fuente": "argentinadatos_riesgo_pais (EMBI+ JP Morgan, en pb)"}


def rem_devaluacion_pct(detalle: dict | None) -> float | None:
    """Devaluacion implicita en el REM = variacion del tipo de cambio
    proyectado prox. 12m vs. el del anio actual. Publica (sin `_`) porque
    la reusa `api.py` para el veredicto "conviene" de /api/devaluacion-
    implicita (Epica 14 Parte 1, 2026-09-14) -- una sola formula, no
    reimplementada en un segundo lugar."""
    if not detalle:
        return None
    tc_prox, tc_actual = detalle.get("tc_usd_prox12m"), detalle.get("tc_usd_anio_actual")
    if tc_prox is None or not tc_actual:
        return None
    return (tc_prox / tc_actual - 1) * 100


def append_macro_historico(con, clave: str, fecha: str, valor: float | None) -> None:
    """Agrega una observacion a la serie de `macro_historico`. Desde la
    Epica 21 (2026-09-29) delega en `indicadores_macro.upsert_serie`:
    `clave` tiene que estar en `indicadores_catalogo`, un dato revisado
    por la fuente pisa al anterior (antes era INSERT OR IGNORE y la
    revision se perdia) y un None nunca pisa nada. Solo la usan los
    indicadores SIN fila propia en benchmarks_macro
    (rem_devaluacion_esperada_12m); el resto entra solo por
    `upsert_benchmark`."""
    if valor is None:
        return
    r = im.upsert_serie(con, clave, [(fecha, valor)])
    if r["filas_actualizadas"]:
        print(f"  {clave}: historial revisado {r['cambios']}")


def upsert_benchmark(con, clave: str, dato: dict | None, ahora: str) -> None:
    """UPSERT generico sobre `benchmarks_macro` (clave/valor_pct/fecha_dato/
    fuente/detalle_json). Publica (sin `_`) porque `cargar_indicadores_
    economia_real.py` (Epica 16, 2026-09-15) y cargar_zpindex.py la reusan
    tal cual -- misma tabla, mismo mecanismo, solo cambian los fetchers --
    en vez de duplicar el UPSERT en un segundo modulo.

    Epica 21 (2026-09-29): si `indicadores_catalogo` tiene un indicador
    con `clave_benchmark` = esta clave, el mismo dato (sin el redondeo a 3
    decimales de benchmarks_macro) entra tambien a su serie en
    `macro_historico`. Reemplaza la 4ta columna a mano que tenia `pasos`
    en `cargar()` y el append explicito de los otros 2 cargadores."""
    if dato is None:
        print(f"  {clave}: SIN DATO, no se actualiza el registro existente (si lo hay).")
        return
    detalle_json = json.dumps(dato["detalle"], ensure_ascii=False) if dato.get("detalle") else None
    con.execute(
        """
        INSERT INTO benchmarks_macro (clave, valor_pct, fecha_dato, fuente, detalle_json, actualizado_en)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (clave) DO UPDATE SET
            valor_pct = excluded.valor_pct,
            fecha_dato = excluded.fecha_dato,
            fuente = excluded.fuente,
            detalle_json = excluded.detalle_json,
            actualizado_en = excluded.actualizado_en;
        """,
        (clave, round(dato["valor_pct"], 3), dato["fecha_dato"], dato["fuente"], detalle_json, ahora),
    )
    extra = f"  detalle={detalle_json}" if detalle_json else ""
    print(f"  {clave}: {dato['valor_pct']:.2f} (dato al {dato['fecha_dato']}, fuente={dato['fuente']}){extra}")

    indicador_id = im.id_por_clave_benchmark(con, clave)
    if indicador_id:
        r = im.upsert_serie(con, indicador_id, [(dato["fecha_dato"], dato["valor_pct"])])
        estado = "nueva" if r["filas_nuevas"] else ("revisada" if r["filas_actualizadas"] else "sin cambio")
        print(f"    historial {indicador_id} @ {dato['fecha_dato']}: {estado}")


def cargar(db_path: Path = DB_PATH) -> None:
    hacer_backup(db_path)

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    ahora = datetime.now().isoformat()

    try:
        con.execute("BEGIN;")

        # El historial de cada benchmark lo escribe `upsert_benchmark`
        # segun `indicadores_catalogo.clave_benchmark` (Epica 21) -- ya no
        # hay una 4ta columna a mano aca.
        pasos = [
            ("Inflacion Argentina interanual", "inflacion_ars_interanual", obtener_inflacion_interanual),
            ("Inflacion Argentina mensual", "inflacion_ars_mensual", obtener_inflacion_mensual),
            ("Devaluacion del peso interanual (USD MEP)", "devaluacion_ars_interanual", obtener_devaluacion_interanual),
            ("S&P 500 interanual", "sp500_interanual", obtener_sp500_interanual),
            ("UST 10Y", "ust_10y_pct", obtener_ust_10y),
            ("T-Bill 3M (tasa libre de riesgo, frontera RV)", "tbill_3m_pct", obtener_tbill_3m),
            ("Concentracion S&P 500 (RSP/SPY)", "sp500_concentracion_pct", obtener_sp500_concentracion),
            ("Emergentes vs. S&P 500 (EEM/SPY)", "em_vs_sp500_ratio", obtener_em_vs_sp500),
            ("Merval", "merval", obtener_merval),
            ("REM (BCRA) -- inflacion esperada / TAMAR / tipo de cambio", "rem", obtener_rem_bcra),
            ("CPI EEUU interanual (BLS)", "us_cpi_interanual", obtener_us_cpi_interanual),
            ("Fed -- EFFR + expectativa proxima reunion", "fed", obtener_fed),
            ("Riesgo pais Argentina (EMBI+)", "riesgo_pais_ar", obtener_riesgo_pais),
        ]
        for titulo, clave, fn in pasos:
            print(f"=== {titulo} ===")
            try:
                dato = fn()
                upsert_benchmark(con, clave, dato, ahora)
                if clave == "rem" and dato is not None:
                    # Unico indicador sin fila propia en benchmarks_macro:
                    # se deriva del detalle del REM.
                    deval_pct = rem_devaluacion_pct(dato.get("detalle"))
                    append_macro_historico(con, "rem_devaluacion_esperada_12m", dato["fecha_dato"], deval_pct)
            except Exception as e:
                print(f"  FALLO: {e}")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nCarga OK. foreign_key_check: sin filas rotas")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


def listar_macro_historico(con, clave: str, desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """Lee la serie historica de `macro_historico` (Epica 12 Parte 4,
    2026-09-12) para UNA clave puntual -- `clave` es obligatorio a
    proposito (mismo criterio que `listar_historico` de
    cargar_mercado_renta_fija_historico.py: son series para graficar
    una a la vez, no un dump de toda la tabla junta, que mezclaria
    escalas totalmente distintas -- pb de riesgo pais vs. % de
    inflacion vs. % de devaluacion esperada)."""
    condiciones, params = ["clave = ?"], [clave]
    if desde:
        condiciones.append("fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("fecha <= ?")
        params.append(hasta)
    filas = con.execute(
        f"SELECT fecha, valor FROM macro_historico WHERE {' AND '.join(condiciones)} ORDER BY fecha;",
        params,
    ).fetchall()
    return [{"fecha": f, "valor": v} for f, v in filas]


if __name__ == "__main__":
    cargar()
