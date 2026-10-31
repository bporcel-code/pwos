"""
PWOS -- logica compartida de valorizacion de posiciones.

Bug critico (sesion 2026-07-14): los bonos soberanos, provinciales, CER,
ONs y LECAPs argentinos cotizan por lamina de VN100 (cada 100 de valor
nominal), no por unidad -- el precio que trae data912 (y que se guarda
tal cual en `precios`, sin tocar) ya viene en esa convencion de mercado.
Sin dividir por 100 al valorizar, el patrimonio en renta fija quedaba
inflado ~100x. Acciones y CEDEARs cotizan por unidad y no llevan este
ajuste.

Centralizado aca (en vez de repetido en dashboard.py y en cualquier
script de verificacion o el futuro bot de Telegram) para no reintroducir
el mismo bug en un segundo lugar.
"""

TIPOS_LAMINA_VN100 = {
    "bono_soberano", "bono_provincial", "bono_cer", "on", "lecap",
}


def valor_posicion_nativo(tipo_activo: str, cantidad: float, precio: float) -> float:
    """Valor de mercado de una posicion, en la moneda nativa del precio
    (todavia sin convertir a USD)."""
    if tipo_activo in TIPOS_LAMINA_VN100:
        return cantidad * precio / 100
    return cantidad * precio


# Confirmado 2026-07-29 cruzando contra el Excel del usuario: convertir
# CEDEARs con MEP los sobreestimaba de forma sistematica y consistente
# (los 13 CEDEARs de la cartera, TODOS por encima del valor real, nunca
# al reves - ~USD 130 de sobreestimacion en conjunto). Usando CCL en vez
# de MEP el error cae a ruido de redondeo/horario (<USD 10 en conjunto).
# Acciones locales (IRSA, PAMP, etc.) NO mejoran con CCL - sus diferencias
# son de otro origen (probablemente precio intradia desactualizado, no un
# problema sistematico de circuito FX) y se quedan en MEP.
TIPOS_CCL = {"cedear"}


def convertir_a_usd(tipo_activo: str, valor_nativo: float, moneda_precio: str, mep: float, ccl: float) -> float:
    """Convierte un valor en moneda nativa a USD, eligiendo la tasa segun
    el tipo de activo (ver TIPOS_CCL)."""
    if moneda_precio == "USD":
        return valor_nativo
    tasa = ccl if tipo_activo in TIPOS_CCL else mep
    return valor_nativo / tasa


# Renta fija / renta variable, para el indicador de composicion del
# dashboard (Tarea 8.4, Tanda 1). 'fci', 'cripto', 'inmueble' y 'fx' NO
# se clasifican en ninguna de las 2 -- caen en "otro" (ver
# categoria_activo) en vez de adivinar: 'fci' hoy tiene cantidad 0 en
# toda la cartera (sin impacto), pero si en el futuro vuelve a tener
# posicion, mejor que aparezca visible como "otro" para que el usuario lo
# clasifique a mano que asumirlo en silencio.
RENTA_FIJA = {"bono_soberano", "bono_provincial", "bono_cer", "on", "lecap"}
RENTA_VARIABLE = {"accion", "cedear"}


def categoria_activo(tipo_activo: str) -> str:
    """'renta_fija' / 'renta_variable' / 'otro' (sin clasificar todavia)."""
    if tipo_activo in RENTA_FIJA:
        return "renta_fija"
    if tipo_activo in RENTA_VARIABLE:
        return "renta_variable"
    return "otro"


def convertir_efectivo_a_usd(saldo: float, moneda_cuenta: str, mep: float, pct_propiedad: float = 100.0) -> float:
    """Convierte un saldo de efectivo a USD. 'USD' y 'USD_CCL' ya estan en
    dolares (USD_CCL es 'dolar cable', un circuito distinto pero sigue
    siendo un dolar real, no una cotizacion en pesos a convertir) - bug
    encontrado 2026-07-29: se estaba dividiendo por MEP como si fuera ARS,
    licuando casi por completo los saldos en USD_CCL (salian en USD 0,00).

    `pct_propiedad` (ADR-032): la parte del contenedor que es del usuario, de
    `pct_propiedad_por_cuenta` -- el saldo llega al 100% (el de la app)."""
    saldo = parte_propia(saldo, pct_propiedad)
    if moneda_cuenta in ("USD", "USD_CCL"):
        return saldo
    return saldo / mep


# ADR-032 (2026-10-26) -- contenedores compartidos: los movimientos se cargan
# al 100% (el saldo de PWOS = el de la app) y la parte propia se aplica al
# valuar. ESTA es la unica fuente del porcentaje: todo codigo que sume
# efectivo, gastos o renta de un contenedor lo lee de aca (nunca repite el
# JOIN a `contenedores.pct_propiedad` por su cuenta). Las vistas de saldo
# NATIVO (cruce contra la app) siguen al 100% y no lo usan.

def pct_propiedad_por_cuenta(con) -> dict[int, float]:
    """{cuenta_id: pct_propiedad del contenedor de esa cuenta}. Sin la
    columna (base anterior a v2.50, o una base de tests armada a mano) da
    {} -- `pct_de` cae a 100."""
    if not any(f[1] == "pct_propiedad" for f in con.execute("PRAGMA table_info(contenedores);")):
        return {}
    return dict(con.execute(
        "SELECT cu.id, co.pct_propiedad FROM cuentas cu JOIN contenedores co ON co.id = cu.contenedor_id;"
    ).fetchall())


def pct_de(pcts: dict[int, float], cuenta_id: int | None) -> float:
    """Porcentaje de una cuenta segun el mapa de `pct_propiedad_por_cuenta`
    (100 si la cuenta no esta o es None)."""
    return pcts.get(cuenta_id, 100.0) if cuenta_id is not None else 100.0


def parte_propia(monto: float, pct_propiedad: float = 100.0) -> float:
    """La parte propia de un monto cargado al 100%."""
    return monto if pct_propiedad == 100.0 else monto * pct_propiedad / 100.0


# ADR-033 (2026-10-27) -- valuacion EN VIVO durante la rueda, cierre diario
# para la historia. ESTE es el unico lugar donde se decide que precio y que
# tipo de cambio "de hoy" usa una valuacion: todo lo que antes leia
# `vista_precio_actual` para valuar pasa por `precio_actual`/`fx_actual`.
# - Por defecto (scripts, reportes, tests) da el ULTIMO CIERRE guardado, como
#   siempre. Solo la API prende `VIVO_HABILITADO`.
# - Lo que es historia o escribe en la base (snapshot de cartera, snapshot de
#   bonos, estimados y conciliacion de cobros) corre dentro de `solo_cierre()`.
# - Si la fuente en vivo falla, `precios_vivo` ya devuelve el cierre: nunca un
#   error ni un 0.

from contextlib import contextmanager
from contextvars import ContextVar

VIVO_HABILITADO = False
_forzar_cierre: ContextVar[bool] = ContextVar("forzar_cierre", default=False)


@contextmanager
def solo_cierre():
    """Dentro de este bloque (o funcion decorada con @solo_cierre()) toda
    valuacion usa el ultimo cierre guardado, aunque la rueda este abierta."""
    token = _forzar_cierre.set(True)
    try:
        yield
    finally:
        _forzar_cierre.reset(token)


def cotizaciones(con) -> dict | None:
    """Estado de `precios_vivo` si corresponde valuar en vivo; None = cierre."""
    if not VIVO_HABILITADO or _forzar_cierre.get():
        return None
    import precios_vivo  # import tardio: los scripts que no valuan en vivo no cargan requests
    return precios_vivo.estado(con)


def cotizaciones_para_pantalla(con) -> dict:
    """De cuando es el dato con el que se esta valuando (para el dashboard)."""
    import precios_vivo
    est = cotizaciones(con)
    if est is None:
        est = precios_vivo._en_cierre(con, "valuación con el último cierre", False)
    return precios_vivo.resumen_para_pantalla(est, con)


def precio_actual(con, activo_id: int) -> tuple[float, str] | None:
    """(precio, moneda) para valuar hoy: el vivo si hay, si no el ultimo cierre."""
    est = cotizaciones(con)
    if est and est["modo"] == "vivo" and activo_id in est["precios"]:
        return est["precios"][activo_id]
    return con.execute(
        "SELECT precio, moneda FROM vista_precio_actual WHERE activo_id = ?;", (activo_id,)
    ).fetchone()


def fx_actual(con, ticker: str) -> float:
    """Tipo de cambio para valuar hoy (USD_MEP/USD_CCL en vivo; el resto, y
    fuera de la rueda, el ultimo cierre guardado)."""
    est = cotizaciones(con)
    if est and est["modo"] == "vivo" and ticker in est["fx"]:
        return est["fx"][ticker]
    row = con.execute("""
        SELECT p.precio FROM vista_precio_actual p
        JOIN activos a ON a.id = p.activo_id
        WHERE a.ticker = ?;
    """, (ticker,)).fetchone()
    if row is None:
        raise ValueError(f"No hay cotización de {ticker} cargada en 'precios'.")
    return row[0]
