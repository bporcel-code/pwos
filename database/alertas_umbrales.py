"""
PWOS -- Epica 12 Parte 2 (2026-09-13): chequeo
automatico de los umbrales estructurados (`tesis_umbrales`, Parte 1)
contra el valor MAS RECIENTE de cada indicador, en cada carga del
dashboard -- sin notificacion push, alcanza con marcar la tesis la
proxima vez que el usuario abra PWOS.

Fuente del "valor actual" por indicador (Epica 21, 2026-09-29): la
ultima observacion de su serie en `macro_historico`, o al vuelo para los
derivados (ccl_mep_brecha_pct, desde `precios`) -- ver
indicadores_macro.valores_actuales. Antes era un mapeo a mano
(`_CLAVE_BENCHMARKS`) hacia `benchmarks_macro` + 2 casos especiales; con
el catalogo, cualquier indicador nuevo se chequea sin tocar este modulo,
con la misma regla que el umbral escrito sobre la tarjeta del indicador
(indicadores_macro.cumple).

Si un indicador no tiene dato disponible (serie vacia, o sin precios de
FX ese dia), el umbral queda con `valor_actual=None` y `cruzado=False` --
nunca se asume "cruzado" sin dato real.
"""
from indicadores_macro import cumple as _cumple
from indicadores_macro import valores_actuales as _valores_actuales


def chequear_umbrales(con, tesis_tabla: str | None = None, tesis_id: int | None = None) -> list[dict]:
    """Devuelve la evaluacion de CADA umbral (no solo los cruzados) --
    el front decide que hacer con `cruzado`. `tesis_tabla` opcional
    filtra a una sola bitacora (asi cada una de las 3 solo trae lo
    suyo). `tesis_id` opcional acota a UNA tesis puntual -- el uso real
    es la tarjeta de esa tesis pidiendo el detalle de sus propios
    umbrales (mismo request sirve para el badge Y para el desglose,
    reemplaza al GET crudo de tesis_umbrales para fines de display)."""
    valores_actuales = _valores_actuales(con)

    condiciones, params = [], []
    if tesis_tabla:
        condiciones.append("tesis_tabla = ?")
        params.append(tesis_tabla)
    if tesis_id is not None:
        condiciones.append("tesis_id = ?")
        params.append(tesis_id)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    filas = con.execute(
        f"""
        SELECT id, tesis_tabla, tesis_id, indicador, operador, valor, accion
        FROM tesis_umbrales
        {where}
        ORDER BY id;
        """,
        params,
    ).fetchall()

    resultado = []
    for umbral_id, tt, tesis_id, indicador, operador, valor_umbral, accion in filas:
        actual = valores_actuales.get(indicador)
        valor_actual = actual["valor"] if actual else None
        fecha_valor_actual = actual["fecha"] if actual else None
        cruzado = _cumple(operador, valor_actual, valor_umbral) if valor_actual is not None else False
        resultado.append({
            "umbral_id": umbral_id,
            "tesis_tabla": tt,
            "tesis_id": tesis_id,
            "indicador": indicador,
            "operador": operador,
            "valor_umbral": valor_umbral,
            "valor_actual": valor_actual,
            "fecha_valor_actual": fecha_valor_actual,
            "cruzado": cruzado,
            "accion": accion,
        })
    return resultado
