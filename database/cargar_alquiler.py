"""
cargar_alquiler.py -- PWOS (Real Estate, 2026-09-17)

Carga un alquiler cobrado para una propiedad/periodo. `monto` es
SIEMPRE la porcion NETA segun el pct_propiedad de esa propiedad (ej. el
50% de una propiedad compartida) -- este script no aplica ningun %, asume que ya
viene neto. `monto_usd` se calcula al MEP de HOY (momento de la carga),
no al MEP historico de `fecha_cobro`.

UNIQUE(propiedad_id, periodo) impide cargar el mismo mes 2 veces -- si
hace falta corregir un monto ya cargado, es un UPDATE manual (SQL
directo), no correr este script de nuevo con otro numero.

Uso (parado en database/):
    python cargar_alquiler.py <alias_propiedad> <periodo AAAA-MM> <monto> <moneda> <fecha_cobro AAAA-MM-DD>

Ejemplo:
    python cargar_alquiler.py DEPTO-EJEMPLO 2025-09 650000 ARS 2025-09-10
"""
import sqlite3
import sys
from pathlib import Path

from alquileres import crear_alquiler
from propiedades import obtener_propiedad_id
from dashboard import obtener_precio_fx
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 6:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    periodo = sys.argv[2]
    monto = float(sys.argv[3])
    moneda = sys.argv[4]
    fecha_cobro = sys.argv[5]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        propiedad_id = obtener_propiedad_id(con, alias)
        if propiedad_id is None:
            print(f"ERROR: no existe la propiedad '{alias.upper()}' -- date de alta primero con alta_propiedad.py.")
            sys.exit(1)

        mep = obtener_precio_fx(con, "USD_MEP")
        try:
            nuevo_id = crear_alquiler(
                con, propiedad_id=propiedad_id, periodo=periodo, monto=monto, moneda=moneda,
                mep=mep, fecha_cobro=fecha_cobro,
            )
        except sqlite3.IntegrityError as e:
            print(f"ABORTADO: ya existe un alquiler cargado para '{alias.upper()}' en {periodo} ({e}). "
                  f"Si hay que corregir el monto, es un UPDATE manual, no una carga nueva.")
            sys.exit(1)

        monto_usd = monto if moneda == "USD" else round(monto / mep, 2)
        print(f"+ alquiler {alias.upper()} {periodo}: {moneda} {monto:,.2f} = USD {monto_usd:,.2f} "
              f"(MEP {mep}, cobrado {fecha_cobro}) -- id {nuevo_id}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
