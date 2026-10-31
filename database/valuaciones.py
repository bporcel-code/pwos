"""
PWOS -- CRUD de `valuaciones` (Real Estate, 2026-09-18, pedido del
usuario). Historial de tasaciones de una `propiedad`, SIEMPRE al 100% del
activo -- `pct_propiedad` se aplica recien al leer (`valuacion_vigente`
devuelve tambien `valor_usd_propio`), nunca se guarda ya multiplicado.

El valor vigente de una propiedad es su fila MAS RECIENTE por `fecha`
(desempate por `id` si hay 2 en la misma fecha, que hoy el `UNIQUE`
del schema no deja pasar salvo un intento de recarga bloqueado). Una
fila nueva nunca pisa ni borra una anterior -- tabla de historial, no
de estado.

IMPORTANTE (ver migrar_v228_valuaciones_pasivos.py y ESTADO_PWOS.md,
sesion 2026-09-18): esta tabla se puebla para todas las propiedades
(incluido un `tipo='fondo_re'`), pero SOLO `valor_inmuebles_usd`
filtra por `tipo='activo_fisico'` -- esa funcion es la unica fuente que
alimenta patrimonio_total (dashboard.py/api.py). Un fondo_re ya esta
contado en patrimonio via su cuenta de efectivo propia; no se lo vuelve
a sumar aca.
"""


def crear_valuacion(con, *, propiedad_id: int, fecha: str, valor_usd: float,
                     fuente: str | None = None) -> int:
    if valor_usd < 0:
        raise ValueError(f"valor_usd invalido: {valor_usd!r} (debe ser >= 0)")
    cur = con.execute(
        """
        INSERT INTO valuaciones (propiedad_id, fecha, valor_usd, fuente)
        VALUES (?, ?, ?, ?);
        """,
        (propiedad_id, fecha, valor_usd, fuente),
    )
    con.commit()
    return cur.lastrowid


def listar_valuaciones(con, propiedad_id: int | None = None) -> list[dict]:
    condicion = "WHERE v.propiedad_id = ?" if propiedad_id is not None else ""
    parametros = (propiedad_id,) if propiedad_id is not None else ()
    filas = con.execute(
        f"""
        SELECT v.id, v.propiedad_id, p.alias, v.fecha, v.valor_usd, v.fuente, v.creado_en
        FROM valuaciones v JOIN propiedades p ON p.id = v.propiedad_id
        {condicion}
        ORDER BY v.fecha DESC, v.id DESC;
        """,
        parametros,
    ).fetchall()
    return [
        {
            "id": r[0], "propiedad_id": r[1], "alias": r[2], "fecha": r[3],
            "valor_usd": r[4], "fuente": r[5], "creado_en": r[6],
        }
        for r in filas
    ]


def valuacion_vigente(con, propiedad_id: int) -> dict | None:
    """Fila de `valuaciones` mas reciente (MAX fecha, desempate por id)
    para una propiedad, con `valor_usd_propio` (= valor_usd x
    pct_propiedad/100) ya calculado -- None si la propiedad todavia no
    tiene ninguna valuacion cargada."""
    row = con.execute(
        """
        SELECT v.id, v.fecha, v.valor_usd, v.fuente, p.pct_propiedad
        FROM valuaciones v JOIN propiedades p ON p.id = v.propiedad_id
        WHERE v.propiedad_id = ?
        ORDER BY v.fecha DESC, v.id DESC
        LIMIT 1;
        """,
        (propiedad_id,),
    ).fetchone()
    if row is None:
        return None
    valuacion_id, fecha, valor_usd, fuente, pct_propiedad = row
    return {
        "id": valuacion_id,
        "fecha": fecha,
        "valor_usd": valor_usd,
        "fuente": fuente,
        "pct_propiedad": pct_propiedad,
        "valor_usd_propio": round(valor_usd * pct_propiedad / 100, 2),
    }


def listar_valor_inmuebles(con) -> list[dict]:
    """Una fila por propiedad `tipo='activo_fisico'` con valuacion
    vigente cargada, con `valor_usd_propio` ya calculado -- UNICA fuente
    que alimenta tanto la rama 'inmuebles' de composicion-jerarquica
    como `total_inmuebles`/patrimonio_total en /api/resumen (no se
    duplica el filtro/calculo en 2 lugares). Filtra por tipo a
    proposito: un 'fondo_re' ya esta contado en
    patrimonio via su cuenta de efectivo propia (ver docstring del
    modulo) -- sumarlo aca lo duplicaria. Propiedades `activo_fisico`
    sin ninguna valuacion cargada todavia no aparecen (no se asume un
    valor)."""
    filas = con.execute(
        "SELECT id, alias FROM propiedades WHERE tipo = 'activo_fisico' ORDER BY alias;"
    ).fetchall()
    resultado = []
    for propiedad_id, alias in filas:
        vigente = valuacion_vigente(con, propiedad_id)
        if vigente is not None:
            resultado.append({"propiedad_id": propiedad_id, "alias": alias, **vigente})
    return resultado


def valor_inmuebles_usd(con) -> float:
    """Suma de `valor_usd_propio` sobre `listar_valor_inmuebles` -- ver
    esa funcion para el criterio de inclusion (solo activo_fisico)."""
    return round(sum(f["valor_usd_propio"] for f in listar_valor_inmuebles(con)), 2)
