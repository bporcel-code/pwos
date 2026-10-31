"""
PWOS -- carga/actualiza el contrato de alquiler VIGENTE de una propiedad
(schema v2.45, columnas `alquiler_*` de `propiedades`). Es lo que usa
flujo_estimado.py para estimar el cobro de cada mes.

`--monto` es SIEMPRE la porcion NETA del usuario (mismo criterio que
`alquileres.monto`). Si el alquiler COMPLETO entra a un contenedor compartido
(ADR-032), el contrato sigue en la parte neta y la conciliacion compara contra
la parte propia del cobro.

Sin `--confirmar` solo muestra el antes/despues (no escribe).

Uso (parado en database/):
    python cargar_contrato_alquiler.py --alias DEPTO-EJEMPLO --monto 650000 \
        --dia 10 --contenedor "Banco Demo" --desde 2025-09-01 [--confirmar]
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()

COLUMNAS = ("alquiler_monto", "alquiler_moneda", "alquiler_dia_cobro", "alquiler_contenedor_id", "alquiler_desde")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--alias", required=True)
    ap.add_argument("--monto", type=float, required=True)
    ap.add_argument("--moneda", default="ARS", choices=("ARS", "USD"))
    ap.add_argument("--dia", type=int, required=True)
    ap.add_argument("--contenedor", required=True, help="nombre exacto en `contenedores`")
    ap.add_argument("--desde", required=True, help="primer cobro a estimar, AAAA-MM-DD")
    ap.add_argument("--confirmar", action="store_true")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args()

    date.fromisoformat(args.desde)  # valida el formato
    if not 1 <= args.dia <= 31:
        raise SystemExit("--dia tiene que estar entre 1 y 31")

    con = sqlite3.connect(args.db)
    prop = con.execute("SELECT id FROM propiedades WHERE alias = ?;", (args.alias,)).fetchone()
    if prop is None:
        raise SystemExit(f"No existe la propiedad {args.alias!r}")
    cont = con.execute("SELECT id FROM contenedores WHERE nombre = ?;", (args.contenedor,)).fetchone()
    if cont is None:
        raise SystemExit(f"No existe el contenedor {args.contenedor!r}")

    antes = con.execute(f"SELECT {', '.join(COLUMNAS)} FROM propiedades WHERE id = ?;", prop).fetchone()
    despues = (args.monto, args.moneda, args.dia, cont[0], args.desde)
    print(f"{args.alias}:")
    for col, a, d in zip(COLUMNAS, antes, despues):
        print(f"  {col:24s} {a!s:>12} -> {d}")

    if not args.confirmar:
        print("(sin --confirmar: no se escribio nada)")
        return
    with con:
        con.execute(
            f"UPDATE propiedades SET {', '.join(c + ' = ?' for c in COLUMNAS)}, "
            "actualizado_en = CURRENT_TIMESTAMP WHERE id = ?;",
            (*despues, prop[0]),
        )
    print("Guardado.")
    con.close()


if __name__ == "__main__":
    main()
