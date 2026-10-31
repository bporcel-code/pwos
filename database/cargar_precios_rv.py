"""
PWOS -- Carga del precio historico en USD para el calculo de retornos de
Renta Variable (Epica 19, Fase 4, 2026-09-22). Puebla `precios_candidatas`
(v2.33, reutilizada -- ver su docstring en create_schema.py).

Universo (mas chico que el de cargar_fundamentales_rv.py): SOLO lo que no
esta ya disponible en `precios`.
  - CEDEARs de la cartera ABIERTA: `precios` tiene su precio en pesos de
    BYMA (via .BA), no el del SUBYACENTE en USD que pide esta fase -- ese
    dato no existe en ningun lado todavia, hay que traerlo.
  - Candidatas de `watchlist_rv` (cualquier tipo): nunca se compraron, no
    tienen ninguna fila en `precios`.
  Las ACCIONES LOCALES NO entran aca: su
  historial en ARS ya esta en `precios` desde 2023 (backfill_precios_
  historico_acciones_cedears_2026-08-27.py) y se convierte a USD con el
  MEP historico al vuelo en retornos_rv.py, sin necesitar cache.

Precio AJUSTADO por dividendos (yfinance `auto_adjust=True`), a proposito
DISTINTO del precio crudo que usa el resto del proyecto para valorizar
posiciones (`backfill_precios_...`, `auto_adjust=False`): aca se calculan
RETORNOS para correlacion/riesgo, no el valor de una tenencia -- con varios
pagadores de dividendo grandes (ej. PEP, KO, JNJ), un precio sin
ajustar mostraria una caida falsa cada fecha ex-dividendo, inflando la
volatilidad y distorsionando la correlacion alrededor de esas fechas.

Acciones LOCALES candidatas (ticker con sufijo ".BA", ej. YPFD.BA): se
descarga el precio en ARS y se convierte a USD con el MEP HISTORICO de la
fecha EXACTA (mismo criterio de "nunca aproximar con el dia mas cercano"
que dashboard.precio_fx_historico) -- una fecha sin MEP exacto se descarta,
no se inventa.

REGLA DE ORO: nada se inventa. Un ticker sin cobertura de Yahoo, o sin MEP
para convertir, queda simplemente sin filas -- retornos_rv.py lo trata
igual que "historial insuficiente".

Uso:
    python cargar_precios_rv.py --dry-run                # descarga y resume, NO escribe
    python cargar_precios_rv.py --dry-run --extra BKNG ZTS YPFD.BA
    python cargar_precios_rv.py                          # carga real (backup previo)
    python cargar_precios_rv.py --ticker BKNG             # solo uno (sin backup)
"""
import argparse
import logging
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import yfinance as yf

from dashboard import calcular_ganancia_por_activo, obtener_precio_fx
import configuracion

logger = logging.getLogger(__name__)

DB_PATH = configuracion.ruta_db()

FUENTE = "yfinance (ajustado)"
PERIODO_DESCARGA = "5y"   # ventana amplia, con margen sobre los 36 meses de "alta confianza"
PAUSA_ENTRE_TICKERS_SEG = 1.0


def es_accion_local(ticker: str) -> bool:
    return ticker.endswith(".BA")


def universo(con, mep: float, ccl: float, extra: list[str] | None = None) -> list[tuple[str, str]]:
    """[(ticker, origen)], origen 'cartera' | 'watchlist' | 'extra'. `ticker` ES el
    simbolo de Yahoo a pedir (para 'cartera' coincide con el ticker de `activos`:
    los CEDEARs ya usan el ticker canonico del subyacente, sin sufijo -- ver
    clasificar_yfinance.simbolo_yfinance)."""
    salida: list[tuple[str, str]] = []
    vistos: set[str] = set()
    for p in sorted(calcular_ganancia_por_activo(con, mep, ccl), key=lambda x: x["ticker"]):
        if p["tipo"] == "cedear" and p["posicion_abierta"]:
            salida.append((p["ticker"], "cartera"))
            vistos.add(p["ticker"])
    for (t,) in con.execute("SELECT ticker FROM watchlist_rv ORDER BY ticker;"):
        if t not in vistos:
            salida.append((t, "watchlist"))
            vistos.add(t)
    for t in extra or []:
        t = t.strip().upper()
        if t not in vistos:
            salida.append((t, "extra"))
            vistos.add(t)
    return salida


def _mep_por_fecha(con) -> dict[str, float]:
    return {
        fecha: precio
        for fecha, precio in con.execute(
            "SELECT p.fecha, p.precio FROM precios p JOIN activos a ON a.id = p.activo_id WHERE a.ticker = 'USD_MEP';"
        )
    }


def descargar_serie(ticker: str, mep_por_fecha: dict[str, float] | None = None) -> tuple[list[tuple[str, float]], str | None]:
    """(filas [(fecha, cierre_usd), ...], motivo si no hay ninguna fila). Para un
    ticker ".BA" convierte con `mep_por_fecha` (fechas sin MEP exacto se
    descartan); para el resto, el cierre de Yahoo YA esta en USD."""
    try:
        hist = yf.Ticker(ticker).history(period=PERIODO_DESCARGA, auto_adjust=True)
    except Exception as e:
        return [], f"error de red/parseo: {str(e)[:120]}"
    if hist is None or hist.empty or "Close" not in hist.columns:
        return [], "Yahoo no devolvió historial (sin cobertura)"

    filas = []
    if es_accion_local(ticker):
        sin_mep = 0
        for fecha_ts, cierre in hist["Close"].items():
            fecha = fecha_ts.strftime("%Y-%m-%d")
            mep = (mep_por_fecha or {}).get(fecha)
            if mep is None:
                sin_mep += 1
                continue
            filas.append((fecha, float(cierre) / mep))
        if not filas:
            return [], f"sin MEP exacto para ninguna de sus {sin_mep} fechas"
    else:
        filas = [(fecha_ts.strftime("%Y-%m-%d"), float(cierre)) for fecha_ts, cierre in hist["Close"].items()]
    return filas, None


def hacer_backup(db_path: Path) -> Path:
    destino = db_path.parent / "backups" / f"{db_path.name}.backup_precios_rv_{datetime.now():%Y-%m-%d_%H%M%S}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, destino)
    c = sqlite3.connect(destino)
    ok = c.execute("PRAGMA integrity_check;").fetchone()[0]
    c.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {destino.name} (integrity_check=ok)")
    return destino


def guardar(con, ticker: str, filas: list[tuple[str, float]]) -> None:
    con.executemany(
        """
        INSERT INTO precios_candidatas (ticker, fecha, cierre_usd)
        VALUES (?, ?, ?)
        ON CONFLICT (ticker, fecha) DO UPDATE SET cierre_usd = excluded.cierre_usd, actualizado_en = CURRENT_TIMESTAMP;
        """,
        [(ticker, fecha, cierre) for fecha, cierre in filas],
    )
    con.commit()


def _resumen(ticker: str, origen: str, filas: list[tuple[str, float]], motivo: str | None) -> str:
    if motivo:
        return f"{ticker:10} {origen:9} SIN DATOS -- {motivo}"
    fechas = sorted(f for f, _ in filas)
    return f"{ticker:10} {origen:9} {len(filas):4} precios  {fechas[0]} -> {fechas[-1]}"


def main() -> int:
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="PWOS - precios historicos en USD para riesgo de Renta Variable (yfinance)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="descarga y muestra, NO escribe nada")
    ap.add_argument("--ticker", help="cargar solo este ticker (simbolo de Yahoo -- no hace falta que ya este en la cartera ni en la watchlist, ver simulador_rv.py)")
    ap.add_argument("--extra", nargs="*", default=[], help="simbolos de Yahoo extra (solo con --dry-run)")
    args = ap.parse_args()
    if args.extra and not args.dry_run:
        ap.error("--extra solo se puede usar con --dry-run (las candidatas reales van a la watchlist)")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    uri = f"file:{args.db.as_posix()}?mode=ro" if args.dry_run else str(args.db)
    con = sqlite3.connect(uri, uri=args.dry_run)
    mep, ccl = obtener_precio_fx(con, "USD_MEP"), obtener_precio_fx(con, "USD_CCL")
    lista = universo(con, mep, ccl, args.extra)
    if args.ticker:
        t = args.ticker.strip().upper()
        # Ad-hoc, mismo criterio que cargar_fundamentales_rv.py --ticker: si
        # ya esta en la cartera/watchlist se reusa su entrada, si no se
        # procesa igual (lo necesita el Simulador de compra para un
        # candidato que todavia no se agrego a ningun lado).
        lista = [x for x in lista if x[0] == t] or [(t, "ticker")]
    print(f"Universo: {len(lista)} tickers | {'DRY-RUN (no escribe)' if args.dry_run else 'carga real'} | base: {args.db}")

    if not args.dry_run and not args.ticker:
        hacer_backup(args.db)

    mep_por_fecha = _mep_por_fecha(con)
    ok, errores = 0, []
    for i, (ticker, origen) in enumerate(lista):
        filas, motivo = descargar_serie(ticker, mep_por_fecha)
        print(_resumen(ticker, origen, filas, motivo))
        if motivo:
            errores.append((ticker, motivo))
        else:
            ok += 1
            if not args.dry_run:
                guardar(con, ticker, filas)
        if i < len(lista) - 1:
            time.sleep(PAUSA_ENTRE_TICKERS_SEG)

    print(f"\nOK: {ok} | sin datos: {len(errores)}"
          + ("" if args.dry_run else f" | escritos en precios_candidatas: {ok}"))
    con.close()
    return 0 if lista else 1


if __name__ == "__main__":
    sys.exit(main())
