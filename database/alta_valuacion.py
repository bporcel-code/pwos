"""
alta_valuacion.py -- PWOS (Real Estate, 2026-09-18)

Carga una tasacion/valuacion nueva para una propiedad -- SIEMPRE al
100% del activo (nunca la parte propia, ver valuaciones.py).
UNIQUE(propiedad_id, fecha) impide cargar 2 veces el mismo dia -- si
hace falta corregir un monto ya cargado, es un UPDATE manual (SQL
directo), no correr este script de nuevo con otro numero.

Uso (parado en database/):
    python alta_valuacion.py <alias_propiedad> <valor_usd> <fecha AAAA-MM-DD> [fuente]

Ejemplos:
    python alta_valuacion.py DEPTO-EJEMPLO 95000 2025-07-31 "zpindex x m2"
    python alta_valuacion.py FONDO-EJEMPLO 1500.00 2025-07-31 "saldo de cuenta del fondo"
"""
import sqlite3
import sys
from pathlib import Path

from valuaciones import crear_valuacion
from propiedades import obtener_propiedad_id
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    valor_usd = float(sys.argv[2])
    fecha = sys.argv[3]
    fuente = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        propiedad_id = obtener_propiedad_id(con, alias)
        if propiedad_id is None:
            print(f"ERROR: no existe la propiedad '{alias.upper()}' -- date de alta primero con alta_propiedad.py.")
            sys.exit(1)

        try:
            nuevo_id = crear_valuacion(con, propiedad_id=propiedad_id, fecha=fecha, valor_usd=valor_usd, fuente=fuente)
        except sqlite3.IntegrityError as e:
            print(f"ABORTADO: ya existe una valuacion cargada para '{alias.upper()}' en {fecha} ({e}). "
                  f"Si hay que corregir el monto, es un UPDATE manual, no una carga nueva.")
            sys.exit(1)

        print(f"+ valuacion {alias.upper()} {fecha}: USD {valor_usd:,.2f}"
              f"{f' (fuente: {fuente})' if fuente else ''} -- id {nuevo_id}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
