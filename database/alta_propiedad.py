"""
alta_propiedad.py -- PWOS (Real Estate, 2026-09-17)

Alta de una propiedad/fondo en el catalogo `propiedades`. INSERT
simple -- se puede correr mas de una vez sin duplicar (alias es UNIQUE,
el script chequea antes de insertar).

Uso (parado en database/):
    python alta_propiedad.py <alias> <tipo> <pct_propiedad> <fecha_alta> [fecha_disponibilidad_capital] [notas]

    tipo: activo_fisico | fondo_re
    fecha_disponibilidad_capital: solo valido si tipo=fondo_re -- omitir o pasar "" si no aplica

Ejemplos:
    python alta_propiedad.py DEPTO-EJEMPLO activo_fisico 100 2025-07-15
    python alta_propiedad.py CASA-EJEMPLO activo_fisico 50 2024-03-01
    python alta_propiedad.py FONDO-EJEMPLO fondo_re 100 2025-02-20
"""
import sqlite3
import sys
from pathlib import Path

from propiedades import crear_propiedad, obtener_propiedad_id
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 5:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    tipo = sys.argv[2]
    pct_propiedad = float(sys.argv[3])
    fecha_alta = sys.argv[4]
    fecha_disponibilidad_capital = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None
    notas = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6] else None

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        existente = obtener_propiedad_id(con, alias)
        if existente is not None:
            print(f"= '{alias.upper()}' ya existe (id {existente}), no se duplica.")
            return
        nuevo_id = crear_propiedad(
            con, alias=alias, tipo=tipo, pct_propiedad=pct_propiedad, fecha_alta=fecha_alta,
            fecha_disponibilidad_capital=fecha_disponibilidad_capital, notas=notas,
        )
        print(f"+ '{alias.upper()}' creada (id {nuevo_id}, tipo={tipo}, pct={pct_propiedad}%, alta={fecha_alta})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
