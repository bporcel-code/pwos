"""
PWOS -- CRUD de `pasivos`/`pagos_pasivo` (Real Estate, 2026-09-18,
pedido del usuario). Deuda puntual (ej. la que financia la compra de una
propiedad) -- NO un sistema general de creditos/hipotecas/
tarjetas, fuera de alcance a proposito.

`monto_original_usd` ya es la parte propia (misma convencion que
`alquileres.monto`). `propiedad_id` es OPCIONAL: un pasivo no
necesariamente esta atado a una propiedad.

Saldo de un pasivo = `monto_original_usd` menos la suma de sus
`pagos_pasivo` -- se calcula al leer, nunca se guarda (mismo criterio
event-sourced que el resto del proyecto, ADR-003). El saldo vigente de
TODOS los pasivos (tengan o no `propiedad_id`) se resta de
patrimonio_total en todo momento, no solo cuando termina de pagarse.
"""


def crear_pasivo(con, *, alias: str, monto_original_usd: float, fecha_alta: str,
                  propiedad_id: int | None = None) -> int:
    if monto_original_usd <= 0:
        raise ValueError(f"monto_original_usd invalido: {monto_original_usd!r} (debe ser > 0)")
    cur = con.execute(
        """
        INSERT INTO pasivos (alias, monto_original_usd, propiedad_id, fecha_alta)
        VALUES (?, ?, ?, ?);
        """,
        (alias.strip(), monto_original_usd, propiedad_id, fecha_alta),
    )
    con.commit()
    return cur.lastrowid


def obtener_pasivo_id(con, alias: str) -> int | None:
    row = con.execute("SELECT id FROM pasivos WHERE alias = ?;", (alias.strip(),)).fetchone()
    return row[0] if row else None


def _saldo(con, pasivo_id: int, monto_original_usd: float) -> float:
    pagado = con.execute(
        "SELECT COALESCE(SUM(monto_usd), 0) FROM pagos_pasivo WHERE pasivo_id = ?;",
        (pasivo_id,),
    ).fetchone()[0]
    return round(monto_original_usd - pagado, 2)


def listar_pasivos(con) -> list[dict]:
    filas = con.execute(
        """
        SELECT pa.id, pa.alias, pa.monto_original_usd, pa.propiedad_id, p.alias, pa.fecha_alta, pa.creado_en
        FROM pasivos pa LEFT JOIN propiedades p ON p.id = pa.propiedad_id
        ORDER BY pa.fecha_alta DESC;
        """
    ).fetchall()
    return [
        {
            "id": r[0], "alias": r[1], "monto_original_usd": r[2], "propiedad_id": r[3],
            "propiedad_alias": r[4], "fecha_alta": r[5], "creado_en": r[6],
            "saldo_usd": _saldo(con, r[0], r[2]),
        }
        for r in filas
    ]


def saldo_total_pasivos_usd(con) -> float:
    """Suma del saldo vigente de TODOS los pasivos -- unica fuente que
    se resta de patrimonio_total (dashboard.py/api.py)."""
    filas = con.execute("SELECT id, monto_original_usd FROM pasivos;").fetchall()
    return round(sum(_saldo(con, pasivo_id, monto) for pasivo_id, monto in filas), 2)


def crear_pago_pasivo(con, *, pasivo_id: int, fecha: str, monto_usd: float) -> int:
    if monto_usd <= 0:
        raise ValueError(f"monto_usd invalido: {monto_usd!r} (debe ser > 0)")
    cur = con.execute(
        "INSERT INTO pagos_pasivo (pasivo_id, fecha, monto_usd) VALUES (?, ?, ?);",
        (pasivo_id, fecha, monto_usd),
    )
    con.commit()
    return cur.lastrowid


def listar_pagos_pasivo(con, pasivo_id: int | None = None) -> list[dict]:
    condicion = "WHERE pp.pasivo_id = ?" if pasivo_id is not None else ""
    parametros = (pasivo_id,) if pasivo_id is not None else ()
    filas = con.execute(
        f"""
        SELECT pp.id, pp.pasivo_id, pa.alias, pp.fecha, pp.monto_usd, pp.creado_en
        FROM pagos_pasivo pp JOIN pasivos pa ON pa.id = pp.pasivo_id
        {condicion}
        ORDER BY pp.fecha DESC;
        """,
        parametros,
    ).fetchall()
    return [
        {"id": r[0], "pasivo_id": r[1], "alias": r[2], "fecha": r[3], "monto_usd": r[4], "creado_en": r[5]}
        for r in filas
    ]
