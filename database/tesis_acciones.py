"""
PWOS -- Tesis de inversion de Renta Variable (2026-09-12, pedido del
usuario: extender a la solapa Analisis - Acciones la misma bitacora que
ya existia solo para Bonos). Wrapper delgado sobre `tesis_bitacora.py`
-- mismo esquema y misma logica que `tesis_bonos.py`, aplicado a
`tesis_acciones` en vez de `tesis_bonos`.

2 tipos de tesis en la MISMA tabla, distinguidos por `ticker`:
  - Macro (ticker NULL): tesis de la cartera de Renta Variable completa.
  - Micro (ticker NOT NULL): tesis de una accion/CEDEAR puntual.

`ticker` es TEXT libre, SIN FK a `activos` -- permite cargar una tesis
sobre un ticker que el usuario esta evaluando comprar, sin posicion todavia.

Por que NO se reusa `tesis_inversion` (v2.13, Epica 11 Herramienta 3):
esa tabla sigue viva y en uso (peso_objetivo_pct para el rebalanceo),
pero es 1-fila-por-activo sin historial ni fecha -- un concepto
distinto a esta bitacora cronologica (mismo motivo por el que
`tesis_bonos` tampoco la reuso, ver ESTADO_PWOS.md 2026-09-11).
"""
from tesis_bitacora import (
    VALORES_CONVICCION,
    VALORES_ESTADO,
    crear_tesis as _crear_tesis,
    actualizar_tesis as _actualizar_tesis,
    eliminar_tesis as _eliminar_tesis,
    listar_tesis as _listar_tesis,
)

TABLA = "tesis_acciones"


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
