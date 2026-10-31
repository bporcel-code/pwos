"""PWOS -- transferencias entre cuentas propias y aporte externo.

Publicable: lo usan `rendimiento.py` (aporte del mes provisorio) y las rondas
incrementales. Los patrones de texto de las patas propias (CUIT, nombre) y
del aporte externo son del usuario: vienen de la configuracion.
"""
import sqlite3
from datetime import datetime

import configuracion


# ---------------------------------------------------------------------------
# Transferencias entre cuentas propias (ronda 2026-10-10):
# no se marcan con una categoria, pero en cada ronda todo lo que va de una
# cuenta propia a otra tiene que netear cero -- cada pata tiene su
# contraparte, del mismo monto y en sentido opuesto, en OTRO contenedor.
# ---------------------------------------------------------------------------
# Patas identificables por su propio texto. La contraparte se busca entre
# TODOS los ingreso/egreso (alguna billetera exporta "Transferencia enviada" sin nombre).
PATRONES_CUENTA_PROPIA = configuracion.get("movimientos", "patrones_cuenta_propia", default=[])
PATRONES_APORTE_EXTERNO = configuracion.get("movimientos", "patrones_aporte_externo", default=[])


def verificar_transferencias_propias(con: sqlite3.Connection, desde: str, hasta: str,
                                     tolerancia_dias: int = 3, desde_id: int = 0) -> dict:
    """Empareja cada pata de transferencia propia con fecha en [desde, hasta]
    (y id > `desde_id`: solo las filas cargadas en la ronda) con su contraparte
    (de esta ronda o de una anterior): sentido opuesto (ingreso <-> egreso), misma moneda,
    mismo monto (al centavo), OTRO contenedor, a lo sumo `tolerancia_dias`
    de distancia (puede caer fuera de la ventana). Emparejamiento 1 a 1, la
    contraparte mas cercana en fecha. Devuelve {"pares": [...], "sin_pareja": [...]}
    -- sin_pareja vacio = netea cero."""
    from movimientos_gastos import PATRONES_EXCLUIDOS  # fondeo/desfondeo con brokers, de los 2 lados
    # Un aporte externo (plata que entra de afuera de PWOS) no tiene contraparte propia.
    patrones = PATRONES_CUENTA_PROPIA + [p for p in PATRONES_EXCLUIDOS if p not in PATRONES_APORTE_EXTERNO]
    sql_base = """
        SELECT m.id, m.fecha, m.tipo, m.monto, m.moneda, m.descripcion,
               COALESCE(co.contenedor_id, cd.contenedor_id) AS contenedor_id
        FROM movimientos m
        LEFT JOIN cuentas co ON co.id = m.cuenta_origen_id
        LEFT JOIN cuentas cd ON cd.id = m.cuenta_destino_id
        WHERE m.tipo IN ('ingreso', 'egreso') AND m.naturaleza = 'efectivo'
    """
    candidatas = con.execute(
        sql_base + " AND m.fecha BETWEEN ? AND ? AND m.id > ? AND ("
        + " OR ".join(["m.descripcion LIKE ?"] * len(patrones)) + ") ORDER BY m.fecha, m.id",
        [desde, hasta, desde_id] + patrones).fetchall()
    def _dia(fecha):
        return datetime.strptime(fecha, "%Y-%m-%d").toordinal()

    # Grafo egreso <-> ingreso: cada candidata con sus contrapartes posibles.
    filas, vecinos = {}, {}
    for fila in candidatas:
        mid, fecha, tipo, monto, moneda, _, contenedor = fila
        filas[mid] = fila
        opuesto = "ingreso" if tipo == "egreso" else "egreso"
        for o in con.execute(
                sql_base + " AND m.tipo = ? AND m.moneda = ? AND ABS(m.monto - ?) < 0.005 "
                           "AND ABS(julianday(m.fecha) - julianday(?)) <= ?",
                (opuesto, moneda, monto, fecha, tolerancia_dias)).fetchall():
            if o[6] == contenedor:
                continue
            filas[o[0]] = o
            egreso, ingreso = (mid, o[0]) if tipo == "egreso" else (o[0], mid)
            vecinos.setdefault(egreso, set()).add(ingreso)

    # Emparejamiento maximo (Kuhn): con montos repetidos el mas cercano no
    # alcanza (un -50.000 de A puede ir a B o a C; un -50.000 de B solo a C)
    # -- se busca la asignacion 1 a 1 que empareja a todos.
    pareja_de_ingreso: dict[int, int] = {}

    def _aumentar(egreso: int, vistos: set) -> bool:
        orden = sorted(vecinos.get(egreso, ()), key=lambda i: (abs(_dia(filas[i][1]) - _dia(filas[egreso][1])), i))
        for ingreso in orden:
            if ingreso in vistos:
                continue
            vistos.add(ingreso)
            if ingreso not in pareja_de_ingreso or _aumentar(pareja_de_ingreso[ingreso], vistos):
                pareja_de_ingreso[ingreso] = egreso
                return True
        return False

    for egreso in sorted(vecinos, key=lambda e: (filas[e][1], e)):
        _aumentar(egreso, set())
    emparejados = set(pareja_de_ingreso) | set(pareja_de_ingreso.values())
    pares = sorted(((filas[e], filas[i]) for i, e in pareja_de_ingreso.items()), key=lambda par: (par[0][1], par[0][0]))
    sin_pareja = [f for f in candidatas if f[0] not in emparejados]
    return {"pares": pares, "sin_pareja": sin_pareja}
