"""
PWOS -- Tesis de inversion de Real Estate (Epica 15 Parte B, 2026-09-15,
pedido del usuario): bitacora CRONOLOGICA de tesis, mismo patron que
`tesis_bonos.py`/`tesis_acciones.py`. Wrapper delgado sobre
`tesis_bitacora.py` -- la logica real vive ahi, este modulo solo fija
tabla='tesis_inmuebles' y con_ticker=True.

2 tipos de tesis en la MISMA tabla, distinguidos por `ticker`:
  - Macro (ticker NULL): tesis de la cartera de inmuebles completa.
  - Micro (ticker NOT NULL): tesis de una propiedad puntual.

`ticker` reusado como identificador CORTO de la propiedad (ej.
"DEPTO-CENTRO") -- texto libre SIN FK, mismo patron que bonos/acciones:
no hay un catalogo separado de propiedades, cada una es su propio
`contenedor` (tipo='inmueble', ver alta_inmueble.py). Se normaliza
(upper + strip) igual que el ticker de un bono.

Sin pestana de Real Estate propia todavia -- esta bitacora vive en el
Dashboard, al lado de `tesis_cartera`, hasta que se construya esa
solapa (Fase 2: flujo de renta mensual vía movimientos tipo='renta';
Fase 3: indicadores ICC/precio por m2).

`contenedor_cobro` (Real Estate Fase 2, 2026-09-15) -- nombre del
contenedor (ej. "Banco X") donde se cobra el alquiler de la
propiedad de esta tesis. Texto libre SIN FK, mismo criterio laxo que
`ticker`. Es exclusivo de esta tabla (las otras 3 bitacoras no tienen
este concepto) por eso se maneja ACA, con una consulta propia despues
de llamar al modulo compartido -- no se sumo a `tesis_bitacora.py` para
no ensuciar el CRUD generico con un campo que solo aplica a inmuebles.
"""
from tesis_bitacora import (
    VALORES_CONVICCION,
    VALORES_ESTADO,
    crear_tesis as _crear_tesis,
    actualizar_tesis as _actualizar_tesis,
    eliminar_tesis as _eliminar_tesis,
    listar_tesis as _listar_tesis,
)

TABLA = "tesis_inmuebles"


def _normalizar_contenedor_cobro(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip().upper()
    return valor or None


def crear_tesis(con, *, ticker: str | None, fecha: str, tesis: str,
                riesgos: str | None = None, conviccion: str | None = None,
                contenedor_cobro: str | None = None) -> int:
    tesis_id = _crear_tesis(con, TABLA, con_ticker=True, ticker=ticker, fecha=fecha,
                             tesis=tesis, riesgos=riesgos, conviccion=conviccion)
    contenedor_cobro = _normalizar_contenedor_cobro(contenedor_cobro)
    if contenedor_cobro is not None:
        con.execute(
            f"UPDATE {TABLA} SET contenedor_cobro = ? WHERE id = ?;",
            (contenedor_cobro, tesis_id),
        )
        con.commit()
    return tesis_id


def actualizar_tesis(con, tesis_id: int, **campos) -> None:
    contenedor_cobro = campos.pop("contenedor_cobro", None)
    _actualizar_tesis(con, TABLA, tesis_id, con_ticker=True, **campos)
    if contenedor_cobro is not None:
        con.execute(
            f"UPDATE {TABLA} SET contenedor_cobro = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?;",
            (_normalizar_contenedor_cobro(contenedor_cobro), tesis_id),
        )
        con.commit()


def eliminar_tesis(con, tesis_id: int) -> None:
    _eliminar_tesis(con, TABLA, tesis_id)


def listar_tesis(con) -> list[dict]:
    filas = _listar_tesis(con, TABLA, con_ticker=True)
    contenedores = dict(con.execute(f"SELECT id, contenedor_cobro FROM {TABLA};").fetchall())
    for fila in filas:
        fila["contenedor_cobro"] = contenedores.get(fila["id"])
    return filas
