"""
cargar_pago_pasivo.py -- PWOS (Real Estate, 2026-09-18)

Carga una cuota pagada de un pasivo, el dia que se paga -- sin
cronograma previo. Ledger de eventos (como `movimientos`): correr este
script 2 veces con la misma fecha/monto por error inserta 2 pagos
reales, no se deduplica solo -- a diferencia de `alquileres`/
`valuaciones`, acá no hay una nocion de "un pago por periodo".

Uso (parado en database/):
    python cargar_pago_pasivo.py "<alias_pasivo>" <monto_usd> <fecha AAAA-MM-DD>

Ejemplo:
    python cargar_pago_pasivo.py "Credito - Casa ejemplo" 350 2025-08-10
"""
import sqlite3
import sys
from pathlib import Path

from pasivos import crear_pago_pasivo, obtener_pasivo_id, listar_pasivos
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    monto_usd = float(sys.argv[2])
    fecha = sys.argv[3]

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        pasivo_id = obtener_pasivo_id(con, alias)
        if pasivo_id is None:
            print(f"ERROR: no existe el pasivo '{alias}' -- date de alta primero con alta_pasivo.py.")
            sys.exit(1)

        nuevo_id = crear_pago_pasivo(con, pasivo_id=pasivo_id, fecha=fecha, monto_usd=monto_usd)

        saldo = next(p["saldo_usd"] for p in listar_pasivos(con) if p["id"] == pasivo_id)
        print(f"+ pago de USD {monto_usd:,.2f} ({fecha}) sobre '{alias}' -- id {nuevo_id}. "
              f"Saldo restante: USD {saldo:,.2f}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
