"""
PWOS -- Brecha CCL/MEP historica (Epica 12 Parte 4, 2026-09-12, pedido
del usuario). El mas simple de los 4 puntos de esta ronda: PWOS ya tiene
la serie diaria completa de `USD_MEP` y `USD_CCL` en `precios` (activo
1 y 126, backfill_fx_diario.py -- 2018-10-29 en
adelante para MEP, 2013-01-02 para CCL), asi que la brecha es una
consulta, no una integracion nueva. NO se guarda en `macro_historico`
-- duplicar series diarias que ya estan completas en `precios` no
aporta nada, se calcula al vuelo en cada request (rapido: join simple
por fecha, un puñado de miles de filas).

`brecha_pct` = (CCL/MEP - 1) * 100, mismo signo/convencion que el
resto de brechas cambiarias del pais (positivo = CCL mas caro que MEP,
el caso normal).
"""

TICKER_MEP = "USD_MEP"
TICKER_CCL = "USD_CCL"


def brecha_ccl_mep_historico(con, desde: str | None = None, hasta: str | None = None) -> list[dict]:
    """1 fila por fecha donde AMBOS (MEP y CCL) tienen precio ese dia --
    sin interpolar fechas faltantes de ningun lado. `desde`/`hasta`
    opcionales (YYYY-MM-DD, inclusive)."""
    condiciones = []
    params: list = [TICKER_MEP, TICKER_CCL]
    if desde:
        condiciones.append("p_mep.fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("p_mep.fecha <= ?")
        params.append(hasta)
    where_extra = (" AND " + " AND ".join(condiciones)) if condiciones else ""

    filas = con.execute(
        f"""
        SELECT p_mep.fecha, p_mep.precio AS mep, p_ccl.precio AS ccl
        FROM precios p_mep
        JOIN activos a_mep ON a_mep.id = p_mep.activo_id AND a_mep.ticker = ?
        JOIN activos a_ccl ON a_ccl.ticker = ?
        JOIN precios p_ccl ON p_ccl.activo_id = a_ccl.id AND p_ccl.fecha = p_mep.fecha
        WHERE p_mep.precio IS NOT NULL AND p_mep.precio != 0{where_extra}
        ORDER BY p_mep.fecha;
        """,
        params,
    ).fetchall()

    return [
        {"fecha": fecha, "mep": mep, "ccl": ccl, "brecha_pct": round((ccl / mep - 1) * 100, 3)}
        for fecha, mep, ccl in filas
    ]


def brecha_ccl_mep_actual(con) -> dict | None:
    """Ultimo valor disponible -- usado por Epica 12 Parte 2 (chequeo
    de umbrales) para `ccl_mep_brecha_pct`, que no vive cacheado en
    ningun lado (a diferencia de riesgo_pais/rem/inflacion)."""
    filas = brecha_ccl_mep_historico(con)
    return filas[-1] if filas else None
