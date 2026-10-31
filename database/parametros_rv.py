"""
PWOS -- parametros de la solapa Analisis - Acciones (v2.31).

Lee/edita la tabla `parametros_rv`: SOLO los umbrales que el usuario calibra
seguido desde la pantalla (topes, banda de rebalanceo, objetivos de
cubeta). Las demas reglas del proyecto siguen como constante documentada
en el modulo que las usa (UMBRAL_REDUNDANCIA en correlacion_rv.py, etc.).

`PARAMETROS_DEFAULT` es el respaldo si la tabla no existe o le falta una
clave (base fresca sin migrar) -- los mismos valores que siembra
migrar_v231_tesis_rv_cubetas.py. Un valor NULL en la tabla es un estado
valido ("[a definir]") y NO cae al default.
"""
import sqlite3

PARAMETROS_DEFAULT: dict[str, float | None] = {
    "tope_posicion_rv_pct": 10.0,
    "tope_posicion_patrimonio_pct": 5.0,
    "tope_sector_rv_pct": 25.0,
    "banda_rebalanceo_pp": 3.0,
    "objetivo_cubeta_valor_min": 30.0,
    "objetivo_cubeta_valor_max": 40.0,
    "objetivo_cubeta_calidad_min": 45.0,
    "objetivo_cubeta_calidad_max": 55.0,
    "objetivo_cubeta_riesgo_max": 15.0,
    "peso_objetivo_rv_patrimonio_pct": None,
    # v2.36 (migrar_v236_umbral_cedear.py): caso B de cedear_moneda.py
    "umbral_cedear_solo_usd_pct": 1.2,
    # v2.38 (migrar_v238_desvio_cedear.py): badge de cedear_caro_barato.py
    "desvio_cedear_alerta_pct": 2.0,
}

# Unicos parametros que admiten NULL (el resto es un umbral operativo).
ADMITEN_NULL = {"peso_objetivo_rv_patrimonio_pct"}

# Pares min/max que no pueden cruzarse.
PARES_MIN_MAX = [
    ("objetivo_cubeta_valor_min", "objetivo_cubeta_valor_max"),
    ("objetivo_cubeta_calidad_min", "objetivo_cubeta_calidad_max"),
]


def leer_parametros(con) -> dict[str, float | None]:
    params = dict(PARAMETROS_DEFAULT)
    try:
        filas = con.execute("SELECT clave, valor FROM parametros_rv;").fetchall()
    except sqlite3.OperationalError:
        return params
    for clave, valor in filas:
        params[clave] = valor
    return params


def listar_parametros(con) -> list[dict]:
    try:
        filas = con.execute("SELECT clave, valor, descripcion FROM parametros_rv ORDER BY clave;").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"clave": c, "valor": v, "descripcion": d} for c, v, d in filas]


def actualizar_parametro(con, clave: str, valor: float | None) -> None:
    """UPDATE de un parametro existente. KeyError si la clave no existe
    (no se crean claves desde la UI), ValueError si el valor no es valido."""
    existe = con.execute("SELECT 1 FROM parametros_rv WHERE clave = ?;", (clave,)).fetchone()
    if existe is None:
        raise KeyError(clave)

    if valor is None:
        if clave not in ADMITEN_NULL:
            raise ValueError(f"'{clave}' no admite valor vacio")
    elif not (0 <= valor <= 100):
        raise ValueError(f"'{clave}' tiene que estar entre 0 y 100 (recibido {valor})")

    actuales = leer_parametros(con)
    actuales[clave] = valor
    for minimo, maximo in PARES_MIN_MAX:
        if actuales[minimo] is not None and actuales[maximo] is not None and actuales[minimo] > actuales[maximo]:
            raise ValueError(f"'{minimo}' no puede ser mayor que '{maximo}'")

    con.execute("UPDATE parametros_rv SET valor = ? WHERE clave = ?;", (valor, clave))
    con.commit()
