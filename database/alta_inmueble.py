"""
alta_inmueble.py — PWOS (Epica 15 Parte B, 2026-09-15)

Alta y actualizacion de valuacion manual de una propiedad de Real
Estate. Generalizacion del script de valuacion manual del fondo inmobiliario (mismo
patron exacto, parametrizado por nombre de contenedor en vez de un
CONTENEDOR fijo) -- el diseno soporta VARIAS propiedades desde el
arranque, no una sola como un fondo inmobiliario.

Cada propiedad = su propio `contenedor` (tipo='inmueble'), creado por
este mismo script en su primera corrida si todavia no existe -- no hay
seed previo por propiedad (a diferencia de un fondo inmobiliario, que ya vivia en
la siembra inicial de contenedores). Un contenedor generico de tipo
'inmueble' que ya exista queda intacto, sin tocar.

Sin ledger descargable (igual que un fondo inmobiliario): el usuario reporta "cuanto
vale esto hoy" cada vez que corre el script, la diferencia contra el
saldo ya cargado se carga como UN movimiento (`renta` si subio de
valor, `egreso` si bajo) -- mismo mecanismo de saldo que el resto del
proyecto usa via `movimientos`, sin tabla nueva ni caso especial en el
dashboard.

--- Limitacion real, igual que un fondo inmobiliario ---
Sin movimientos reales no hay forma de distinguir "subio de valor" de
"el usuario metio plata nueva" (ej. una mejora/refaccion grande). Mientras
el numero reportado sea "cuanto vale la propiedad hoy" y cualquier
aporte de capital nuevo se cargue aparte como `ingreso`/`egreso` real
(no mezclado en la diferencia de valuacion), el numero de ganancia/
perdida calculado es confiable.

Fase 1 (esta): alta + valuacion manual, sin flujo de renta. El alquiler
mensual de la propiedad co-poseida va en Fase 2, via movimientos
tipo='renta' atados al contenedor -- no confundir esa `renta` (cobro
periodico real) con el `renta`/`egreso` que este script inserta para
la DIFERENCIA de valuacion (mismo nombre de tipo, uso distinto, igual
ambiguedad que ya conviven en el resto del proyecto).

Uso (parado en database/):
    python alta_inmueble.py "<nombre contenedor>" <valor_usd> [fecha AAAA-MM-DD]

Ejemplos:
    python alta_inmueble.py "CASA EJEMPLO" 120000
    python alta_inmueble.py "DEPTO EJEMPLO" 95000 2025-07-31
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()


def obtener_o_crear_contenedor(con: sqlite3.Connection, nombre: str) -> int:
    row = con.execute("SELECT id FROM contenedores WHERE nombre = ?;", (nombre,)).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO contenedores (nombre, tipo, activo) VALUES (?, 'inmueble', 1);",
        (nombre,),
    )
    print(f"  + contenedor '{nombre}' (tipo='inmueble') creado")
    return cur.lastrowid


def obtener_o_crear_cuenta(con: sqlite3.Connection, contenedor_id: int) -> int:
    row = con.execute(
        "SELECT id FROM cuentas WHERE contenedor_id = ? AND moneda = 'USD' AND estado = 'disponible';",
        (contenedor_id,),
    ).fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO cuentas (contenedor_id, moneda, estado) VALUES (?, 'USD', 'disponible');",
        (contenedor_id,),
    )
    print("  + cuenta USD/disponible creada")
    return cur.lastrowid


def saldo_actual(con: sqlite3.Connection, cuenta_id: int) -> float:
    row = con.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN cuenta_destino_id = ? THEN monto END), 0)
             - COALESCE(SUM(CASE WHEN cuenta_origen_id = ? THEN monto END), 0)
        FROM movimientos WHERE cuenta_origen_id = ? OR cuenta_destino_id = ?;
        """,
        (cuenta_id, cuenta_id, cuenta_id, cuenta_id),
    ).fetchone()
    return round(row[0], 2)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    nombre_contenedor = sys.argv[1].strip()
    valor_nuevo = round(float(sys.argv[2]), 2)
    fecha = sys.argv[3] if len(sys.argv) > 3 else date.today().isoformat()

    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        con.execute("BEGIN;")

        contenedor_id = obtener_o_crear_contenedor(con, nombre_contenedor)
        cuenta_id = obtener_o_crear_cuenta(con, contenedor_id)
        saldo_previo = saldo_actual(con, cuenta_id)
        diferencia = round(valor_nuevo - saldo_previo, 2)

        es_primera_carga = saldo_previo == 0
        if diferencia == 0:
            print(f"  = sin cambios, '{nombre_contenedor}' ya esta en USD {valor_nuevo:,.2f}")
        else:
            if es_primera_carga:
                tipo = "ingreso"
                descripcion = (
                    f"Alta de propiedad '{nombre_contenedor}': valuacion inicial USD {valor_nuevo:,.2f} "
                    f"(reportado por el usuario, sin ledger descargable)"
                )
            else:
                tipo = "renta" if diferencia > 0 else "egreso"
                descripcion = (
                    f"Actualizacion manual de valuacion '{nombre_contenedor}': "
                    f"USD {saldo_previo:,.2f} -> USD {valor_nuevo:,.2f} (reportado por el usuario)"
                )
            origen = cuenta_id if diferencia < 0 else None
            destino = cuenta_id if diferencia > 0 else None
            con.execute(
                """
                INSERT INTO movimientos
                    (fecha, tipo, cuenta_origen_id, cuenta_destino_id, monto, moneda, descripcion)
                VALUES (?, ?, ?, ?, ?, 'USD', ?);
                """,
                (fecha, tipo, origen, destino, abs(diferencia), descripcion),
            )
            print(f"  + {tipo} de USD {abs(diferencia):,.2f} ({fecha}): "
                  f"USD {saldo_previo:,.2f} -> USD {valor_nuevo:,.2f}")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("OK.")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
