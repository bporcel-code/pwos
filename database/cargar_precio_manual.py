"""
cargar_precio_manual.py -- PWOS

Carga a mano el precio de un activo que todavia no tiene cotizacion en
ninguna fuente (ej. una ON recien licitada), con `fuente = 'manual'`.
Cuando el activo empiece a cotizar, el feed diario (importador_precios.py)
carga filas con fecha mas nueva y `vista_precio_actual` toma esas: el
precio manual queda solo como historico, no hay que borrarlo.

Usa la misma escala que el resto de `precios` para ese tipo de activo:
bonos/ONs por cada 100 de valor nominal (ej. 100 = 1 USD por nominal).

Backup + transaccion; aborta si ya hay un precio para ese activo y fecha.

Uso (parado en database/):
    python cargar_precio_manual.py <ticker> <fecha AAAA-MM-DD> <precio> <moneda>

Ejemplo (una ON recien licitada, a la par como la muestra el broker
hasta que cotice):
    python cargar_precio_manual.py PN36O 2026-10-02 100 USD
"""
import shutil
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()


def main() -> None:
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)
    ticker, fecha, precio, moneda = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4].upper()
    date.fromisoformat(fecha)
    if precio <= 0 or moneda not in ("ARS", "USD"):
        raise ValueError("precio tiene que ser > 0 y moneda ARS o USD")

    backup = DB_PATH.parent / "backups" / f"pwos_backup_pre-precio-manual_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(DB_PATH, backup)
    print(f"Backup: {backup}")

    con = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        fila = con.execute("SELECT id FROM activos WHERE ticker = ?", (ticker,)).fetchone()
        if fila is None:
            raise ValueError(f"no existe el activo {ticker!r}")
        if con.execute("SELECT 1 FROM precios WHERE activo_id = ? AND fecha = ?", (fila[0], fecha)).fetchone():
            raise ValueError(f"{ticker} ya tiene precio el {fecha}: no se pisa")
        con.execute("BEGIN;")
        con.execute("INSERT INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, ?, ?, ?, 'manual')",
                    (fila[0], fecha, precio, moneda))
        con.execute("COMMIT;")
        print(f"OK: {ticker} {fecha} {precio} {moneda} (fuente manual)")
    finally:
        con.close()


if __name__ == "__main__":
    main()
