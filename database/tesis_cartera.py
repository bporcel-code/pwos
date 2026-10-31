"""
PWOS -- Tesis/metas de la cartera completa (2026-09-12, pedido del
usuario: "para plantear metas y objetivos" a nivel de todo el portafolio,
visible siempre en la solapa Dashboard). Wrapper delgado sobre
`tesis_bitacora.py` -- mismos campos que `tesis_bonos`/`tesis_acciones`
(tesis/riesgos/conviccion/estado + reflexion_final al cerrar), pero SIN
columna `ticker`: aca no hay nocion de "micro" -- esa granularidad ya
la cubren las otras 2 bitacoras. Cada fila es una entrada sobre la
cartera como un todo (ej. una meta de patrimonio, un cambio de
estrategia, una reflexion sobre asignacion de activos).

Bitacora cronologica: cerrar una entrada no la borra, le agrega
`reflexion_final` + `fecha_reflexion` (UPDATE de la misma fila). Sin
filtros server-side -- el front trae todo y filtra en JS, mismo
criterio que las otras 2 bitacoras.
"""
from tesis_bitacora import (
    VALORES_CONVICCION,
    VALORES_ESTADO,
    crear_tesis as _crear_tesis,
    actualizar_tesis as _actualizar_tesis,
    eliminar_tesis as _eliminar_tesis,
    listar_tesis as _listar_tesis,
)

TABLA = "tesis_cartera"


def crear_tesis(con, *, fecha: str, tesis: str,
                riesgos: str | None = None, conviccion: str | None = None) -> int:
    return _crear_tesis(con, TABLA, con_ticker=False, ticker=None, fecha=fecha,
                         tesis=tesis, riesgos=riesgos, conviccion=conviccion)


def actualizar_tesis(con, tesis_id: int, **campos) -> None:
    campos.pop("ticker", None)
    _actualizar_tesis(con, TABLA, tesis_id, con_ticker=False, **campos)


def eliminar_tesis(con, tesis_id: int) -> None:
    _eliminar_tesis(con, TABLA, tesis_id)


def listar_tesis(con) -> list[dict]:
    return _listar_tesis(con, TABLA, con_ticker=False)
