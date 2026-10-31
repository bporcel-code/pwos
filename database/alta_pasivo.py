"""
alta_pasivo.py -- PWOS (Real Estate, 2026-09-18)

Alta de una deuda puntual en `pasivos` (ej. la que financia la compra de
una propiedad) -- NO un sistema general de creditos/hipotecas/tarjetas.
`monto_original_usd` ya es la parte propia (misma convencion que
`alquileres.monto`). El vinculo a una propiedad es OPCIONAL.

INSERT simple -- se puede correr mas de una vez sin duplicar (alias es
UNIQUE, el script chequea antes de insertar).

Uso (parado en database/):
    python alta_pasivo.py "<alias>" <monto_original_usd> <fecha_alta AAAA-MM-DD> [alias_propiedad]

Ejemplo:
    python alta_pasivo.py "Credito - Casa ejemplo" 40000 2025-07-15 CASA-EJEMPLO
"""
import sqlite3
import sys
from pathlib import Path

from pasivos import crear_pasivo, obtener_pasivo_id
from propiedades import obtener_propiedad_id
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    monto_original_usd = float(sys.argv[2])
    fecha_alta = sys.argv[3]
    alias_propiedad = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        existente = obtener_pasivo_id(con, alias)
        if existente is not None:
            print(f"= '{alias}' ya existe (id {existente}), no se duplica.")
            return

        propiedad_id = None
        if alias_propiedad is not None:
            propiedad_id = obtener_propiedad_id(con, alias_propiedad)
            if propiedad_id is None:
                print(f"ERROR: no existe la propiedad '{alias_propiedad.upper()}' -- "
                      f"date de alta primero con alta_propiedad.py.")
                sys.exit(1)

        nuevo_id = crear_pasivo(
            con, alias=alias, monto_original_usd=monto_original_usd,
            fecha_alta=fecha_alta, propiedad_id=propiedad_id,
        )
        vinculo = f", propiedad={alias_propiedad.upper()}" if alias_propiedad else ""
        print(f"+ pasivo '{alias}' creado (id {nuevo_id}, monto USD {monto_original_usd:,.2f}, "
              f"alta={fecha_alta}{vinculo})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
