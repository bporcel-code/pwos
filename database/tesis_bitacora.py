"""
PWOS -- Logica compartida de las 3 bitacoras de tesis de inversion
(2026-09-12: extender la bitacora que ya existia solo
para Bonos a la cartera general y a Acciones).

Las 3 bitacoras (`tesis_cartera`, `tesis_bonos`, `tesis_acciones`) son
el MISMO concepto -- cronologica, macro/micro via `ticker`, cerrar
agrega `reflexion_final` sin crear fila nueva -- aplicado a 3 alcances
distintos (cartera completa / renta fija / renta variable). A
diferencia de `tesis_inversion` (Renta Variable, v2.13, 1-fila-por-
activo sin historial -- un concepto DISTINTO, no se reusa aca tampoco,
ver ESTADO_PWOS.md 2026-09-11), estas 3 SI comparten la misma logica,
por eso este modulo centraliza el CRUD -- cada tabla tiene su propio
modulo delgado (`tesis_cartera.py`/`tesis_bonos.py`/`tesis_acciones.py`)
que solo fija `TABLA` y `CON_TICKER`.

`tabla` nunca viene de input de usuario -- siempre un literal fijo
pasado por el modulo delgado (whitelist implicita), nunca se arma con
datos externos. Se interpola en el SQL porque sqlite3 no permite
parametrizar nombres de tabla/columna.

`con_ticker=True` (bonos/acciones): la tabla tiene columna `ticker`,
soporta macro (NULL) y micro (NOT NULL). `con_ticker=False` (cartera):
no hay columna `ticker` -- toda fila es "la cartera completa", sin
nocion de macro/micro.
"""

VALORES_CONVICCION = ("alta", "media", "baja")
VALORES_ESTADO = ("vigente", "cerrada")

_CAMPOS_ACTUALIZABLES = (
    "fecha", "tesis", "riesgos", "conviccion", "estado", "reflexion_final", "fecha_reflexion",
)


def _normalizar_ticker(ticker: str | None) -> str | None:
    if ticker is None:
        return None
    ticker = ticker.strip().upper()
    return ticker or None


def crear_tesis(con, tabla: str, *, con_ticker: bool, ticker: str | None, fecha: str, tesis: str,
                riesgos: str | None = None, conviccion: str | None = None) -> int:
    """Inserta una tesis nueva, siempre en estado 'vigente'. Devuelve el id nuevo."""
    if not tesis or not tesis.strip():
        raise ValueError("'tesis' no puede estar vacia")
    if conviccion is not None and conviccion not in VALORES_CONVICCION:
        raise ValueError(f"conviccion invalida: {conviccion!r} (valores validos: {VALORES_CONVICCION})")

    if con_ticker:
        cur = con.execute(
            f"""
            INSERT INTO {tabla} (ticker, fecha, tesis, riesgos, conviccion, estado)
            VALUES (?, ?, ?, ?, ?, 'vigente');
            """,
            (_normalizar_ticker(ticker), fecha, tesis, riesgos, conviccion),
        )
    else:
        cur = con.execute(
            f"""
            INSERT INTO {tabla} (fecha, tesis, riesgos, conviccion, estado)
            VALUES (?, ?, ?, ?, 'vigente');
            """,
            (fecha, tesis, riesgos, conviccion),
        )
    con.commit()
    return cur.lastrowid


def actualizar_tesis(con, tabla: str, tesis_id: int, *, con_ticker: bool, **campos) -> None:
    """Update parcial -- solo toca los campos pasados con valor no-None
    (los ausentes/None se ignoran, NO se pisan a NULL). Sirve tanto para
    editar el contenido de una tesis vigente como para cerrarla
    (estado='cerrada' + reflexion_final + fecha_reflexion) en la MISMA
    llamada."""
    fila = con.execute(f"SELECT id FROM {tabla} WHERE id = ?;", (tesis_id,)).fetchone()
    if fila is None:
        raise ValueError(f"tesis id={tesis_id} no existe")

    conviccion = campos.get("conviccion")
    if conviccion is not None and conviccion not in VALORES_CONVICCION:
        raise ValueError(f"conviccion invalida: {conviccion!r} (valores validos: {VALORES_CONVICCION})")
    estado = campos.get("estado")
    if estado is not None and estado not in VALORES_ESTADO:
        raise ValueError(f"estado invalido: {estado!r} (valores validos: {VALORES_ESTADO})")
    tesis_texto = campos.get("tesis")
    if tesis_texto is not None and not tesis_texto.strip():
        raise ValueError("'tesis' no puede quedar vacia")

    nombres = _CAMPOS_ACTUALIZABLES
    if con_ticker:
        if "ticker" in campos:
            campos["ticker"] = _normalizar_ticker(campos["ticker"])
        nombres = ("ticker",) + nombres

    sets, valores = [], []
    for nombre in nombres:
        if nombre in campos and campos[nombre] is not None:
            sets.append(f"{nombre} = ?")
            valores.append(campos[nombre])
    if not sets:
        return  # nada para actualizar -- no es un error, solo un no-op
    sets.append("actualizado_en = CURRENT_TIMESTAMP")
    valores.append(tesis_id)
    con.execute(f"UPDATE {tabla} SET {', '.join(sets)} WHERE id = ?;", valores)
    con.commit()


def eliminar_tesis(con, tabla: str, tesis_id: int) -> None:
    con.execute(f"DELETE FROM {tabla} WHERE id = ?;", (tesis_id,))
    con.commit()


def listar_tesis(con, tabla: str, *, con_ticker: bool) -> list[dict]:
    """Lista cronologica completa (mas reciente primero). Con
    `con_ticker=True`, `ticker=None` marca una tesis macro y `tipo`
    ("macro"/"micro") es un campo derivado, comodo para filtrar/
    renderizar en el front sin repetir la logica de `ticker is None`."""
    columnas = "id, fecha, tesis, riesgos, conviccion, estado, reflexion_final, fecha_reflexion, creado_en, actualizado_en"
    if con_ticker:
        columnas = "id, ticker, " + columnas.split(", ", 1)[1]
    filas = con.execute(
        f"SELECT {columnas} FROM {tabla} ORDER BY fecha DESC, id DESC;"
    ).fetchall()

    if con_ticker:
        return [
            {
                "id": r[0], "ticker": r[1], "tipo": "micro" if r[1] is not None else "macro",
                "fecha": r[2], "tesis": r[3], "riesgos": r[4], "conviccion": r[5], "estado": r[6],
                "reflexion_final": r[7], "fecha_reflexion": r[8], "creado_en": r[9], "actualizado_en": r[10],
            }
            for r in filas
        ]
    return [
        {
            "id": r[0],
            "fecha": r[1], "tesis": r[2], "riesgos": r[3], "conviccion": r[4], "estado": r[5],
            "reflexion_final": r[6], "fecha_reflexion": r[7], "creado_en": r[8], "actualizado_en": r[9],
        }
        for r in filas
    ]
