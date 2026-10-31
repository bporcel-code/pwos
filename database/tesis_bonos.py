"""
PWOS -- Tesis de inversion de bonos (Epica 11, punto 3, 2026-09-12,
pedido del usuario): bitacora CRONOLOGICA de tesis, acotada a la solapa de
Bonos. Wrapper delgado sobre `tesis_bitacora.py` (2026-09-12, refactor
al extender el mismo patron a `tesis_cartera`/`tesis_acciones`) -- la
logica real vive ahi, este modulo solo fija tabla='tesis_bonos' y
con_ticker=True. Comportamiento identico al de antes del refactor.

2 tipos de tesis en la MISMA tabla, distinguidos por `ticker`:
  - Macro (ticker NULL): tesis de la cartera de renta fija completa.
  - Micro (ticker NOT NULL): tesis de un bono puntual.

`ticker` es TEXT libre, SIN FK a `activos` -- mismo patron que
`mercado_renta_fija_rava`/`mercado_renta_fija_historico`: una tesis
puede escribirse sobre un bono que el usuario esta EVALUANDO comprar,
todavia sin posicion. Se normaliza (upper + strip).

Por que NO se reusa `tesis_inversion` (la de Renta Variable, v2.13):
esa tabla es 1-fila-por-activo (PK=activo_id, UPSERT), sin fecha de
historial, sin estado, sin reflexion -- un concepto distinto (ver
ESTADO_PWOS.md, 2026-09-11).

Sin filtros server-side (por estado/ticker) a proposito: el front trae
TODO con `listar_tesis` y filtra en JS, mismo criterio que "Bonos en
tenencia"/"Mercado de bonos".
"""
from tesis_bitacora import (
    VALORES_CONVICCION,
    VALORES_ESTADO,
    crear_tesis as _crear_tesis,
    actualizar_tesis as _actualizar_tesis,
    eliminar_tesis as _eliminar_tesis,
    listar_tesis as _listar_tesis,
)

TABLA = "tesis_bonos"


def crear_tesis(con, *, ticker: str | None, fecha: str, tesis: str,
                riesgos: str | None = None, conviccion: str | None = None) -> int:
    return _crear_tesis(con, TABLA, con_ticker=True, ticker=ticker, fecha=fecha,
                         tesis=tesis, riesgos=riesgos, conviccion=conviccion)


def actualizar_tesis(con, tesis_id: int, **campos) -> None:
    _actualizar_tesis(con, TABLA, tesis_id, con_ticker=True, **campos)


def eliminar_tesis(con, tesis_id: int) -> None:
    _eliminar_tesis(con, TABLA, tesis_id)


def listar_tesis(con) -> list[dict]:
    return _listar_tesis(con, TABLA, con_ticker=True)
