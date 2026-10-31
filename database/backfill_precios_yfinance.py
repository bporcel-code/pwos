"""
PWOS -- Backfill de precio HISTORICO diario de acciones/CEDEARs via
yfinance (2026-08-27), para permitir un ancla de Rendimiento cerca del
1-ene de cada anio (mostrar YTD ademas del acumulado
total en el header). El backfill de bonos de la sesion anterior
(`backfill_precios_historico_bonos_rava_2026-08-26.py`) dejo afuera
este hueco -- confirmado que NINGUNA fecha entre el 1-ene y el
13-jul-2026 calificaba como ancla completa porque acciones/CEDEARs
nunca tuvieron precio historico cargado (solo bonos).

Fuente: `yfinance.Ticker(f"{ticker}.BA").history(auto_adjust=False)` --
el sufijo ".BA" da el precio REAL del CEDEAR/accion en BYMA, en pesos
-- CONFIRMADO cruzando contra data912 (fuente ya usada para el precio
de HOY): CEDEARs y acciones locales coinciden en orden de magnitud y
tendencia con data912 para las mismas fechas. A diferencia de los
bonos, ACA NO HAY AMBIGUEDAD de moneda -- todas las acciones/CEDEARs
de la cartera cotizan en pesos via este sufijo, sin variante en
dolares que confundir (esa ambiguedad era especifica de los bonos "O/D").
`auto_adjust=False` a proposito: se usa el precio REAL que cotizo ese
dia (columna `Close` sin reescalar por splits futuros), no un valor
ajustado a la base de acciones de HOY -- necesario porque `cantidad` en
`operaciones_activos` es el conteo REAL de nominales en cada fecha, sin
ajustar por splits (hay tickers con splits reales). Cruzado contra un
precio ya cargado a mano despues de un split -- coincide exacto.

Universo: TODA accion/cedear que aparecio alguna vez en
operaciones_activos -- no solo vigentes hoy, mismo
criterio que bonos. Ventana por ticker: desde su PRIMERA compra real
hasta hoy (no "todo lo que haya", como bonos) -- reduce exposicion a
datos viejos de baja liquidez sin necesidad real (la cartera nunca
tuvo estos tickers antes de esa fecha).

Sondeo de sanidad hecho ANTES de escribir este script (no incluido en
el repo): casi todos los tickers con cobertura real y limpia (0 precios
negativos/cero, 0 picos aislados con el mismo detector que bonos).
Casos que pueden aparecer:
  - Un ticker sin cobertura real en yfinance (1 sola fila, hoy) -- mismo
    tratamiento que los bonos sin cobertura, no se inventa nada.
  - Un CEDEAR iliquido con la mayoria de los dias al MISMO precio exacto
    (la fuente repite el ultimo precio conocido). Se inserta igual (es
    el dato real disponible), documentado como caveat.
  - Una accion local con un quiebre PERMANENTE de precio (ej. 120 ->
    13,93, ratio real ~8,6x, que no calza con el "Stock Splits" que
    reporta yfinance para esa fecha) -- no es un pico aislado (no
    revierte al dia siguiente, es un nivel nuevo sostenido), se trata
    como evento real de mercado/corporativo, no como glitch.

Salvaguardas de siempre: backup + integrity_check antes, INSERT OR
IGNORE (nunca pisa un precio ya cargado de otra fuente), todo en una
unica transaccion al final.

Uso:
    python backfill_precios_historico_acciones_cedears_2026-08-27.py
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import yfinance as yf
import configuracion

DB_PATH = configuracion.ruta_db()

SIN_COBERTURA_CONOCIDA = set(configuracion.get("precios_yfinance", "sin_cobertura", default=[]))


def tickers_a_backfillear(con) -> list[tuple[int, str, str]]:
    """(activo_id, ticker, primera_compra) de toda accion/cedear que
    aparecio alguna vez en operaciones_activos."""
    filas = con.execute("""
        SELECT a.id, a.ticker, MIN(m.fecha)
        FROM operaciones_activos oa
        JOIN movimientos m ON m.id = oa.movimiento_id
        JOIN activos a ON a.id = oa.activo_id
        WHERE a.tipo IN ('accion', 'cedear') AND m.tipo = 'compra_activo'
        GROUP BY a.id
        ORDER BY a.ticker;
    """).fetchall()
    return [(activo_id, ticker, desde) for activo_id, ticker, desde in filas if ticker not in SIN_COBERTURA_CONOCIDA]


def descargar_historico(ticker: str, desde: str) -> list[dict]:
    """[{fecha, precio}, ...] desde la fecha de la primera compra real
    hasta hoy. Lista vacia si yfinance no tiene cobertura."""
    yft = f"{ticker.replace('.', '-')}.BA"
    hist = yf.Ticker(yft).history(start=desde, auto_adjust=False)
    if hist.empty:
        return []
    return [
        {"fecha": fecha.strftime("%Y-%m-%d"), "precio": float(precio)}
        for fecha, precio in hist["Close"].items()
    ]


def main():
    con = sqlite3.connect(DB_PATH)
    tickers = tickers_a_backfillear(con)
    print(f"Tickers a pedir a yfinance: {len(tickers)}")

    resultados: dict[str, tuple[int, list[dict]]] = {}
    for i, (activo_id, ticker, desde) in enumerate(tickers):
        try:
            datos = descargar_historico(ticker, desde)
        except Exception as e:
            print(f"[{i+1}/{len(tickers)}] {ticker}: ERROR ({e}), se salta")
            continue
        if not datos:
            print(f"[{i+1}/{len(tickers)}] {ticker}: sin cobertura, se salta")
        else:
            resultados[ticker] = (activo_id, datos)
            print(f"[{i+1}/{len(tickers)}] {ticker}: {len(datos)} filas, {datos[0]['fecha']} a {datos[-1]['fecha']}")

    total_filas = sum(len(datos) for _, datos in resultados.values())
    print(f"\nTickers con datos: {len(resultados)}/{len(tickers)}. Total filas a insertar (antes de dedup): {total_filas}")

    if not resultados:
        print("Nada para insertar. Cierro sin tocar la base.")
        con.close()
        return

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-precios-historico-acciones-cedears_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    if backup_path.exists():
        raise RuntimeError(f"{backup_path} ya existe - no lo piso, revisar a mano")
    shutil.copy2(DB_PATH, backup_path)
    con_chk = sqlite3.connect(backup_path)
    ok = con_chk.execute("PRAGMA integrity_check;").fetchone()[0]
    con_chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"\nBackup OK: {backup_path} (integrity_check=ok)")

    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("BEGIN;")
    insertados = 0
    try:
        for ticker, (activo_id, datos) in resultados.items():
            for fila in datos:
                cur = con.execute(
                    "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) "
                    "VALUES (?, ?, ?, 'ARS', 'yfinance_historico');",
                    (activo_id, fila["fecha"], fila["precio"]),
                )
                insertados += cur.rowcount

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"\nCOMMIT OK. Filas insertadas: {insertados} (de {total_filas} descargadas -- la diferencia son fechas que ya tenian precio de otra fuente, no pisadas).")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
