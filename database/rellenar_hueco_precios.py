"""
Relleno del hueco de `precios` 2026-08-28 -> 2026-09-22 (pedido del usuario,
2026-09-23). Diagnostico completo en ESTADO_PWOS.md (entrada del 2026-09-23):
hasta el 09-13 el feed de acciones/CEDEARs era MANUAL (no habia job; el
backfill de yfinance del 08-27 llegaba hasta el 08-27), y despues el job de
n8n fallo por causas puntuales (API caida, backup del dia ya existente, PC
apagada, timeout de data912). data912 solo da el precio de HOY, asi que un
dia perdido no se recupera solo: hace falta una fuente HISTORICA.

Fuentes (las MISMAS que los backfills anteriores, para que la serie sea
consistente):
  - acciones/CEDEARs: yfinance `<ticker>.BA`, columna `Close` con
    auto_adjust=False (precio real en pesos en BYMA) -- ver
    backfill_precios_historico_acciones_cedears_2026-08-27.py. fuente =
    'yfinance_historico'.
  - USD MEP / CCL: api.argentinadatos.com (dolares/bolsa, contadoconliqui),
    dias corridos -- ver backfill_fx_diario_completo_2026-08-31.py.
    fuente = 'argentinadatos_historico_diario'.
Solo se insertan pares (activo, fecha) que NO existen (INSERT OR IGNORE:
nunca pisa un dato de otra fuente).

Validacion cruzada ANTES de escribir: en las fechas donde ya hay dato de
data912/dolarapi (09-04, 09-13 a 09-20) se compara contra la fuente
historica y se reporta el desvio; ademas se buscan saltos diarios anomalos
(> 25%) en lo descargado. Aborta si la mediana del desvio supera
MAX_DESVIO_MEDIANO_PCT.

Uso (parado en database/):
    python backfill_hueco_precios_2026-09-23.py            # dry-run (solo lectura)
    python backfill_hueco_precios_2026-09-23.py --apply    # backup + transaccion + checks
"""
import shutil
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
import yfinance as yf
import configuracion

DB_PATH = configuracion.ruta_db()
def _arg(nombre: str) -> str:
    if nombre in sys.argv[:-1]:
        return sys.argv[sys.argv.index(nombre) + 1]
    raise SystemExit(f"falta {nombre} AAAA-MM-DD (uso: --desde AAAA-MM-DD --hasta AAAA-MM-DD [--apply])")


DESDE, HASTA = _arg("--desde"), _arg("--hasta")
MEP_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"
CCL_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/contadoconliqui"
MAX_DESVIO_MEDIANO_PCT = 1.5
SALTO_ANOMALO_PCT = 25.0
APPLY = "--apply" in sys.argv


def dias_calendario(desde: str, hasta: str):
    d = date.fromisoformat(desde)
    while d <= date.fromisoformat(hasta):
        yield d.isoformat()
        d += timedelta(days=1)


def universo(con) -> list[tuple[int, str]]:
    """Acciones/CEDEARs que ya tienen serie (fila el 2026-08-27)."""
    return con.execute("""
        SELECT a.id, a.ticker FROM activos a
        WHERE a.tipo IN ('accion','cedear')
          AND EXISTS (SELECT 1 FROM precios p WHERE p.activo_id=a.id AND p.fecha='2026-08-27')
        ORDER BY a.ticker;""").fetchall()


def descargar_yf(ticker: str) -> dict[str, float]:
    yft = f"{ticker.replace('.', '-')}.BA"
    ini = (date.fromisoformat(DESDE) - timedelta(days=7)).isoformat()
    fin = (date.fromisoformat(HASTA) + timedelta(days=1)).isoformat()
    hist = yf.Ticker(yft).history(start=ini, end=fin, auto_adjust=False)
    return {ts.strftime("%Y-%m-%d"): float(v) for ts, v in hist["Close"].dropna().items()} if not hist.empty else {}


def descargar_fx(url: str) -> dict[str, float]:
    r = requests.get(url, timeout=30, headers={"User-Agent": "pwos-backfill"})
    r.raise_for_status()
    return {f["fecha"]: float(f["venta"]) for f in r.json() if DESDE <= f["fecha"] <= HASTA and f.get("venta")}


def main():
    con = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    existentes = {(a, f) for a, f in con.execute("SELECT activo_id, fecha FROM precios WHERE fecha BETWEEN ? AND ?;", (DESDE, HASTA))}
    filas_existentes = {(a, f): (p, fu) for a, f, p, fu in con.execute(
        "SELECT activo_id, fecha, precio, fuente FROM precios WHERE fecha BETWEEN ? AND ?;", (DESDE, HASTA))}
    nuevas: list[tuple[int, str, float, str]] = []
    desvios, saltos, sin_datos = [], [], []

    # ---- acciones / CEDEARs (yfinance) ----
    uni = universo(con)
    print(f"Acciones/CEDEARs a completar: {len(uni)} tickers, ventana {DESDE} a {HASTA}")
    for aid, ticker in uni:
        try:
            serie = descargar_yf(ticker)
        except Exception as e:
            sin_datos.append((ticker, f"ERROR {e}"))
            continue
        if not serie:
            sin_datos.append((ticker, "sin cobertura en yfinance"))
            continue
        fechas = sorted(serie)
        for a, b in zip(fechas, fechas[1:]):
            if abs(serie[b] / serie[a] - 1) * 100 > SALTO_ANOMALO_PCT and DESDE <= b <= HASTA:
                saltos.append((ticker, a, b, serie[a], serie[b]))
        for f in dias_calendario(DESDE, HASTA):
            if f not in serie:
                continue
            if (aid, f) in existentes:
                p, fu = filas_existentes[(aid, f)]
                if fu in ("data912", "dolarapi"):
                    desvios.append((ticker, f, (serie[f] / p - 1) * 100))
                continue
            nuevas.append((aid, f, serie[f], "yfinance_historico", ticker))

    # ---- FX (argentinadatos) ----
    fx_desv = []
    for tk, url in (("USD_MEP", MEP_URL), ("USD_CCL", CCL_URL)):
        aid = con.execute("SELECT id FROM activos WHERE ticker=?;", (tk,)).fetchone()[0]
        serie = descargar_fx(url)
        for f in dias_calendario(DESDE, HASTA):
            if f not in serie:
                continue
            if (aid, f) in existentes:
                p, fu = filas_existentes[(aid, f)]
                if fu == "dolarapi":
                    fx_desv.append((tk, f, (serie[f] / p - 1) * 100))
                continue
            nuevas.append((aid, f, serie[f], "argentinadatos_historico_diario", tk))

    # ---- reporte ----
    print(f"\nFilas NUEVAS a insertar: {len(nuevas)}")
    por_tk: dict[str, list[str]] = {}
    for _, f, _, _, tk in nuevas:
        por_tk.setdefault(tk, []).append(f)
    for tk, fs in sorted(por_tk.items()):
        print(f"  {tk:8} {len(fs):2} fechas: {fs[0]} .. {fs[-1]}")
    if sin_datos:
        print("\nSin datos:", sin_datos)
    dm = [abs(d) for _, _, d in desvios]
    print(f"\nValidacion cruzada acciones/CEDEARs vs data912 ({len(dm)} pares en fechas con ambas fuentes):")
    if dm:
        print(f"  |desvio| mediano {statistics.median(dm):.2f}%   p90 {sorted(dm)[int(.9*len(dm))-1]:.2f}%   max {max(dm):.2f}%")
        peores = sorted(desvios, key=lambda x: -abs(x[2]))[:5]
        print("  peores 5:", [(t, f, round(d, 2)) for t, f, d in peores])
    fm = [abs(d) for _, _, d in fx_desv]
    if fm:
        print(f"Validacion cruzada FX vs dolarapi ({len(fm)} pares): |desvio| mediano {statistics.median(fm):.3f}%  max {max(fm):.3f}%")
    print(f"Saltos diarios > {SALTO_ANOMALO_PCT:.0f}% dentro de lo descargado: {saltos or 'ninguno'}")

    problemas = []
    if not dm:
        problemas.append("no hay pares de validacion cruzada")
    elif statistics.median(dm) > MAX_DESVIO_MEDIANO_PCT:
        problemas.append(f"desvio mediano {statistics.median(dm):.2f}% > {MAX_DESVIO_MEDIANO_PCT}%")
    if saltos:
        problemas.append("saltos anomalos")
    if problemas:
        print("\nABORTO (no se escribe nada):", "; ".join(problemas))
        sys.exit(1)
    if not APPLY:
        print("\nDRY RUN: nada se escribio. Correr con --apply para insertar.")
        return
    con.close()

    # ---- escritura ----
    backup = DB_PATH.parent / "backups" / f"pwos_backup_pre-hueco-precios_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(DB_PATH, backup)
    chk = sqlite3.connect(backup)
    ok = chk.execute("PRAGMA integrity_check;").fetchone()[0]
    chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto: {ok}")
    print(f"\nBackup OK: {backup.name} (integrity_check=ok)")
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        con.execute("BEGIN;")
        n = 0
        for aid, f, precio, fuente, _tk in nuevas:
            n += con.execute("INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, ?, ?, 'ARS', ?);",
                             (aid, f, precio, fuente)).rowcount
        ic = con.execute("PRAGMA integrity_check;").fetchone()[0]
        fk = con.execute("PRAGMA foreign_key_check;").fetchall()
        if ic != "ok" or fk:
            raise RuntimeError(f"integrity_check={ic}, foreign_key_check={fk}")
        con.execute("COMMIT;")
        print(f"COMMIT OK. Filas insertadas: {n} de {len(nuevas)} planeadas.")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
