"""
PWOS -- CRUD de `gastos_propiedad` (Real Estate, 2026-09-19, pedido del
usuario). Gastos reales de una propiedad (mantenimiento, impuesto) --
ledger de eventos como `movimientos`/`pagos_pasivo`, sin UNIQUE (2
gastos el mismo dia son 2 filas legitimas, no un error de carga
duplicada).

`monto_usd` es SIEMPRE la porcion NETA que puso el usuario (misma convencion
que `alquileres.monto`/`pasivos.monto_original_usd`) -- responsabilidad
de quien carga el dato, esta capa no aplica ningun %.
"""


def crear_gasto_propiedad(con, *, propiedad_id: int, fecha: str, tipo: str,
                           monto_usd: float, concepto: str | None = None) -> int:
    if tipo not in ("mantenimiento", "impuesto"):
        raise ValueError(f"tipo invalido: {tipo!r} (valores validos: 'mantenimiento', 'impuesto')")
    if monto_usd <= 0:
        raise ValueError(f"monto_usd invalido: {monto_usd!r} (debe ser > 0)")
    cur = con.execute(
        "INSERT INTO gastos_propiedad (propiedad_id, fecha, tipo, monto_usd, concepto) VALUES (?, ?, ?, ?, ?);",
        (propiedad_id, fecha, tipo, monto_usd, concepto),
    )
    con.commit()
    return cur.lastrowid


def listar_gastos_propiedad(con, propiedad_id: int | None = None) -> list[dict]:
    condicion = "WHERE g.propiedad_id = ?" if propiedad_id is not None else ""
    parametros = (propiedad_id,) if propiedad_id is not None else ()
    filas = con.execute(
        f"""
        SELECT g.id, g.propiedad_id, p.alias, g.fecha, g.tipo, g.monto_usd, g.concepto, g.creado_en
        FROM gastos_propiedad g JOIN propiedades p ON p.id = g.propiedad_id
        {condicion}
        ORDER BY g.fecha DESC;
        """,
        parametros,
    ).fetchall()
    return [
        {
            "id": r[0], "propiedad_id": r[1], "alias": r[2], "fecha": r[3], "tipo": r[4],
            "monto_usd": r[5], "concepto": r[6], "creado_en": r[7],
        }
        for r in filas
    ]
