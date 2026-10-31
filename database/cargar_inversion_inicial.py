"""
cargar_inversion_inicial.py -- PWOS (Real Estate -- inversion inicial +
TIR/VPN, 2026-09-19)

Carga/actualiza `propiedades.inversion_inicial_usd` -- capital propio
puesto para adquirir/construir la propiedad (NO aplica a una propiedad
100% financiada por un pasivo -- para esas se
deja en NULL, nunca se carga un 0). Reusa `fecha_alta` (ya existente)
como fecha de ese flujo en flujo_inmueble.py.

REGLA IMPORTANTE (fix 2026-09-19: una primera carga la violo sin
querer): `inversion_inicial_usd` es SIEMPRE la porcion NETA que puso
el usuario, nunca el costo total de compra/construccion de la propiedad
-- MISMA convencion que `alquileres.monto` y `pasivos.monto_original_
usd`. Si la propiedad es co-poseida (ej. construida mitad y mitad con
otra persona), el numero que se carga aca ya tiene
que venir dividido por `pct_propiedad` -- este script NO aplica ningun
% ni lo valida contra `pct_propiedad`, es responsabilidad de quien
carga el dato (igual que en alquileres.py/pasivos.py).

Si se pasa `nota`, se APPENDEA a `propiedades.notas` (no la pisa) --
para dejar constancia de la procedencia del numero (ej. "estimado de
memoria, sin comprobantes") como DATO consultable, no solo como
comentario de este script que nadie vuelve a leer una vez corrido.

Uso (parado en database/):
    python cargar_inversion_inicial.py <alias_propiedad> <inversion_inicial_usd> [nota]
    python cargar_inversion_inicial.py <alias_propiedad> NULL

Ejemplo (una propiedad al 50%: se carga la mitad propia, nunca el total):
    python cargar_inversion_inicial.py CASA-EJEMPLO 40000 "Estimado, sin comprobantes: mitad propia del costo de obra."
"""
import sqlite3
import sys
from pathlib import Path

from propiedades import obtener_propiedad_id, actualizar_inversion_inicial
import configuracion

DB_PATH = configuracion.ruta_db()


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    alias = sys.argv[1]
    valor_raw = sys.argv[2]
    nota = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None

    inversion_inicial_usd = None if valor_raw.upper() == "NULL" else float(valor_raw)

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        propiedad_id = obtener_propiedad_id(con, alias)
        if propiedad_id is None:
            print(f"ERROR: no existe la propiedad '{alias.upper()}' -- date de alta primero con alta_propiedad.py.")
            sys.exit(1)

        try:
            actualizar_inversion_inicial(con, propiedad_id, inversion_inicial_usd)
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)

        if nota:
            notas_previas = con.execute("SELECT notas FROM propiedades WHERE id = ?;", (propiedad_id,)).fetchone()[0]
            notas_nuevas = f"{notas_previas}\n{nota}" if notas_previas else nota
            con.execute("UPDATE propiedades SET notas = ? WHERE id = ?;", (notas_nuevas, propiedad_id))
            con.commit()

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        valor_txt = "NULL" if inversion_inicial_usd is None else f"USD {inversion_inicial_usd:,.2f}"
        print(f"+ {alias.upper()}: inversion_inicial_usd = {valor_txt}{' (+ nota agregada)' if nota else ''}")
        print("OK.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
