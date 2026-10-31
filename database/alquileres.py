"""
PWOS -- CRUD de `alquileres` (Real Estate, 2026-09-17, pedido del
usuario). Convencion IMPORTANTE: `monto`/`moneda` es SIEMPRE la porcion
NETA ya aplicado `pct_propiedad` de la propiedad (ej. el 50% de una
propiedad compartida, nunca el 100% del alquiler) --
responsabilidad de quien carga el dato, esta capa no recalcula el %.

`monto_usd` se fija al MEP del momento de la CARGA (`mep`, pasado por
el llamador -- no busca un historico de `fecha_cobro`, a diferencia de
`calendario_flujo.py::flujos_otros_pasivos`) -- una vez cargado, el
numero es inmutable, no se revisita despues.
"""


def crear_alquiler(con, *, propiedad_id: int, periodo: str, monto: float, moneda: str,
                    mep: float, fecha_cobro: str) -> int:
    monto_usd = monto if moneda == "USD" else monto / mep
    cur = con.execute(
        """
        INSERT INTO alquileres (propiedad_id, periodo, monto, moneda, monto_usd, fecha_cobro)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (propiedad_id, periodo, monto, moneda, round(monto_usd, 2), fecha_cobro),
    )
    con.commit()
    return cur.lastrowid


def listar_alquileres(con, propiedad_id: int | None = None) -> list[dict]:
    condicion = "WHERE a.propiedad_id = ?" if propiedad_id is not None else ""
    parametros = (propiedad_id,) if propiedad_id is not None else ()
    filas = con.execute(
        f"""
        SELECT a.id, a.propiedad_id, p.alias, a.periodo, a.monto, a.moneda, a.monto_usd, a.fecha_cobro, a.creado_en
        FROM alquileres a JOIN propiedades p ON p.id = a.propiedad_id
        {condicion}
        ORDER BY a.fecha_cobro DESC;
        """,
        parametros,
    ).fetchall()
    return [
        {
            "id": r[0], "propiedad_id": r[1], "alias": r[2], "periodo": r[3], "monto": r[4],
            "moneda": r[5], "monto_usd": r[6], "fecha_cobro": r[7], "creado_en": r[8],
        }
        for r in filas
    ]
