"""
PWOS -- Umbrales de monitoreo sobre una tesis de inversion (Epica 12
Parte 1, 2026-09-12). Cada fila es una condicion estructurada
(indicador/operador/valor) + una accion sugerida en texto corto,
adjunta a una tesis puntual de cualquiera de las 3 bitacoras
(`tesis_cartera`/`tesis_bonos`/`tesis_acciones`, identificadas por
`tesis_tabla`+`tesis_id`, ver migrar_v221_tesis_umbrales.py para el
porque de este diseno). El campo `riesgos` de texto libre de la tesis
sigue existiendo tal cual -- esto es un agregado estructurado, no un
reemplazo.

Este modulo es solo el CRUD de las reglas; el chequeo contra los feeds
esta en alertas_umbrales.py (Epica 12 Parte 2).
"""
from indicadores_macro import validar_indicador

TABLAS_VALIDAS = ("cartera", "bonos", "acciones", "inmuebles")
# Epica 21 (2026-09-29): la lista de indicadores validos ya no vive aca
# (antes `INDICADORES_VALIDOS`, copia a mano del CHECK SQL): se valida
# contra `indicadores_catalogo` (indicadores_macro.validar_indicador), asi
# cualquier indicador nuevo del catalogo queda disponible para las tesis
# sin migracion ni cambio de codigo.
OPERADORES_VALIDOS = ("mayor_que", "menor_que")


def _validar_tesis_tabla(tesis_tabla: str) -> None:
    if tesis_tabla not in TABLAS_VALIDAS:
        raise ValueError(f"tesis_tabla invalida: {tesis_tabla!r} (validas: {TABLAS_VALIDAS})")


def crear_umbral(con, *, tesis_tabla: str, tesis_id: int, indicador: str,
                  operador: str, valor: float, accion: str) -> int:
    _validar_tesis_tabla(tesis_tabla)
    validar_indicador(con, indicador)
    if operador not in OPERADORES_VALIDOS:
        raise ValueError(f"operador invalido: {operador!r} (validos: {OPERADORES_VALIDOS})")
    if not accion or not accion.strip():
        raise ValueError("'accion' no puede estar vacia")

    tabla_tesis = {
        "cartera": "tesis_cartera", "bonos": "tesis_bonos",
        "acciones": "tesis_acciones", "inmuebles": "tesis_inmuebles",
    }[tesis_tabla]
    fila = con.execute(f"SELECT id FROM {tabla_tesis} WHERE id = ?;", (tesis_id,)).fetchone()
    if fila is None:
        raise ValueError(f"No existe tesis id={tesis_id} en {tabla_tesis}")

    cur = con.execute(
        """
        INSERT INTO tesis_umbrales (tesis_tabla, tesis_id, indicador, operador, valor, accion)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (tesis_tabla, tesis_id, indicador, operador, valor, accion.strip()),
    )
    con.commit()
    return cur.lastrowid


def listar_umbrales_de_tesis(con, tesis_tabla: str, tesis_id: int) -> list[dict]:
    _validar_tesis_tabla(tesis_tabla)
    filas = con.execute(
        """
        SELECT id, indicador, operador, valor, accion, creado_en
        FROM tesis_umbrales
        WHERE tesis_tabla = ? AND tesis_id = ?
        ORDER BY id;
        """,
        (tesis_tabla, tesis_id),
    ).fetchall()
    return [
        {"id": r[0], "indicador": r[1], "operador": r[2], "valor": r[3], "accion": r[4], "creado_en": r[5]}
        for r in filas
    ]


def eliminar_umbral(con, umbral_id: int) -> None:
    con.execute("DELETE FROM tesis_umbrales WHERE id = ?;", (umbral_id,))
    con.commit()


def eliminar_umbrales_de_tesis(con, tesis_tabla: str, tesis_id: int) -> None:
    """Limpieza al borrar una tesis completa -- sin esto quedarian
    umbrales huerfanos (sin FK real que los borre en cascada, ver
    docstring del modulo). Llamado explicito desde cada endpoint DELETE
    de las 3 bitacoras en api.py, nunca automatico/oculto."""
    _validar_tesis_tabla(tesis_tabla)
    con.execute(
        "DELETE FROM tesis_umbrales WHERE tesis_tabla = ? AND tesis_id = ?;",
        (tesis_tabla, tesis_id),
    )
    con.commit()
