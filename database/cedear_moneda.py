"""
PWOS -- "¿CEDEAR en pesos o en dolares?" (2026-09-24, solapa Analisis - Acciones).

Un CEDEAR cotiza en BYMA en pesos (NFLX), dolar MEP (NFLXD) y cable (NFLXC):
el mismo papel con libros de ordenes separados, asi que sus precios no son
siempre equivalentes. Esta herramienta dice cual de las dos puntas (ARS o
USD MEP) esta mas barata EN ESTE MOMENTO. Replica la planilla del asesor del
usuario ("CEDEARS - Comprar en dolares o pesos?", hoja `Cedear $ o US$`):

    dolar_implicito = ask_ars / ask_usd          # a que TC "compra dolares" el CEDEAR
    spread_pct      = (ask_usd * mep / ask_ars - 1) * 100   # = mep / dolar_implicito - 1

El ratio del CEDEAR y el precio en NY NO intervienen: al ser el mismo papel
en las dos monedas, el ratio se cancela.

Reglas (las de la planilla):
  A. Pesos Y dolares: spread > 0 -> pesos; spread < 0 -> dolares; = 0 -> indiferente.
  B. Solo dolares: spread > umbral (parametros_rv.umbral_cedear_solo_usd_pct,
     1,2 % por defecto = costo de la vuelta por MEP) -> vender USD via MEP y
     comprar en pesos; si no, comprar directo la especie D.
  C. Solo pesos: siempre pesos (sin calculo).

Casos que se BLOQUEAN Y MARCAN (sin recomendacion), convencion del proyecto:
  - `sin_punta`: falta la punta vendedora de alguna de las dos especies o es 0.
    NUNCA se usa el ultimo operado (`c`) como sustituto de una punta.
  - `par_sospechoso`: el dolar implicito se aleja mas de DESVIO_MAX_VS_MEP del
    MEP. Medido el 2026-09-24 sobre 315 pares del panel: mediana -0,19 %, solo
    2 pares (YELP, ING) fuera de +-10 %, ambos con volumen 0 en la D. Un par asi
    es un libro vacio/desactualizado o una especie D mal derivada, no una
    oportunidad.
  - `sin_especie_d` / `no_cotiza`: el ticker (o su D) no esta en el panel.

Fuente: data912.com/live/arg_cedears (trae px_bid/px_ask/q_bid/q_ask ademas de
`c`; las especies D estan en el MISMO panel). data912 no informa la hora de
cada cotizacion: la unica marca de tiempo es el header `Date` de la respuesta
(cache-control max-age=30 el 2026-09-24). Por eso la UI muestra esa hora y un
aviso fijo de confirmar las puntas en el broker.

Calculo en vivo, no guarda nada (mismo criterio que el simulador de compra).

Fase 2 (v2.37) -- comparacion contra Nueva York, con el ratio de `ratios_cedear`
(`ratio` = CEDEARs por accion) y el precio de NY de data912 (formulas de la
planilla del asesor, hoja `Relacion ARG-EEUU`):

    precio_teorico_ars = precio_ny * ccl / ratio
    precio_teorico_usd = precio_ny / ratio * (ccl / mep)   # la D se liquida en MEP, no en cable
    ny_implicito_ars   = ask_ars * ratio / ccl
    ny_implicito_usd   = ask_usd * ratio * mep / ccl
    prima_ars_pct      = (ny_implicito_ars / precio_ny - 1) * 100
    prima_usd_pct      = (ny_implicito_usd / precio_ny - 1) * 100
    ccl_implicito      = ask_ars * ratio / precio_ny          # (base de la Fase 3)

Control de split: ratio_implicito = precio_ny * ccl / ask_ars; si se aleja mas
de DESVIO_MAX_RATIO del ratio de la tabla -> `ratio_desactualizado` y NO se
muestra la prima (un split no reflejado en la lista de BYMA daria una prima
absurda, no una oportunidad).

precio_ny = ultimo operado (`c`) de data912 `usa_stocks` + `usa_adrs` (mismo
proveedor, paneles disjuntos: los ADR como DEO/TSM solo estan en el segundo).
data912 NO tiene panel de ETFs (SPY, QQQ, EEM...): para una fila con
`tipo = 'etf'` que no este en data912 el precio sale de yfinance (ultimo precio,
~15 min de demora; decision del usuario 2026-09-24), rotulado `yfinance`. Cualquier
otro ticker ausente queda `sin_precio_ny`: no se busca en otra fuente.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import configuracion
from dry_run_precios import DATA912_BASE, _get

PANEL_CEDEARS = "arg_cedears"
PANELES_NY = ("usa_stocks", "usa_adrs")
UMBRAL_SOLO_USD_DEFAULT = 1.2
DESVIO_MAX_VS_MEP = 0.10
DESVIO_MAX_RATIO = 0.15
TZ_AR = timezone(timedelta(hours=-3))

# Especie D que NO sale de la regla general (ver `especie_d`). Vacio hoy: la
# regla "X.D si existe, si no XD" cubrio los 315 pares medidos el 2026-09-24.
ESPECIE_D_OVERRIDE: dict[str, str] = {}

# Ticker de Yahoo (watchlist_rv) -> ticker BYMA del CEDEAR en pesos, para los
# que no coinciden. Lo que no esta aca se busca tal cual; si no cotiza en el
# panel queda `no_cotiza` (no se adivina). La tabla completa llega con
# `ratios_cedear` (Fase 2).
TICKER_YAHOO_A_BYMA = {
    "BRK-B": "BRKB",
    "BAC": "BA.C",
    "DIS": "DISN",
    "GOLD": "B",   # Barrick: GOLD es el ticker viejo, en BYMA hoy es B
}


def evaluar(ask_ars: float | None, ask_usd: float | None, mep: float,
            umbral_solo_usd: float = UMBRAL_SOLO_USD_DEFAULT) -> dict:
    """Funcion pura: evalua una compra dadas las dos puntas vendedoras y el MEP."""
    if not mep or mep <= 0:
        raise ValueError(f"MEP invalido: {mep!r}")
    base = {"dolar_implicito": None, "spread_pct": None, "desvio_vs_mep_pct": None,
            "recomendacion_a": None, "recomendacion_b": None, "recomendacion_c": None,
            "umbral_solo_usd_pct": umbral_solo_usd}
    if not ask_ars or not ask_usd or ask_ars <= 0 or ask_usd <= 0:
        return {**base, "estado": "sin_punta"}

    dolar_implicito = ask_ars / ask_usd
    spread_pct = (ask_usd * mep / ask_ars - 1) * 100
    desvio = dolar_implicito / mep - 1
    base.update(dolar_implicito=dolar_implicito, spread_pct=spread_pct, desvio_vs_mep_pct=desvio * 100)
    if abs(desvio) > DESVIO_MAX_VS_MEP:
        return {**base, "estado": "par_sospechoso"}

    # Se compara el spread redondeado para que un caso "justo en el umbral"
    # no dependa del ultimo bit del float.
    s = round(spread_pct, 9)
    rec_a = "pesos" if s > 0 else "dolares" if s < 0 else "indiferente"
    rec_b = "pesos_via_mep" if s > umbral_solo_usd else "dolares"
    return {**base, "estado": "ok", "recomendacion_a": rec_a, "recomendacion_b": rec_b,
            "recomendacion_c": "pesos"}


def ticker_byma(ticker: str) -> str:
    """Normaliza lo que llega de la cartera, la watchlist o el texto libre al
    ticker BYMA del CEDEAR en pesos. `B.D` (especie D cargada como activo
    propio) vuelve a `B`. Un ticker terminado en 'D' SIN punto no se toca:
    `BBD` es Bradesco en pesos, no la D de BB."""
    t = (ticker or "").strip().upper()
    if t.endswith(".D"):
        t = t[:-2]
    return TICKER_YAHOO_A_BYMA.get(t, t)


def especie_d(ticker: str, simbolos) -> str | None:
    """Especie en dolares MEP: override explicito; si no `X.D` (B.D, BB.D,
    C.D, CAR.D -- donde `XD` es otra cosa o no existe); si no `XD`."""
    if ticker in ESPECIE_D_OVERRIDE:
        return ESPECIE_D_OVERRIDE[ticker]
    for candidato in (f"{ticker}.D", f"{ticker}D"):
        if candidato in simbolos:
            return candidato
    return None


def obtener_panel_puntas() -> tuple[dict[str, dict], str | None]:
    """{symbol: item} del panel de CEDEARs + hora del dato (header `Date` en
    hora argentina, ISO). Reusa el cliente con reintentos de dry_run_precios."""
    r = _get(f"{DATA912_BASE}/{PANEL_CEDEARS}")
    panel = {item["symbol"]: item for item in r.json()}
    fecha = None
    if r.headers.get("date"):
        try:
            fecha = parsedate_to_datetime(r.headers["date"]).astimezone(TZ_AR).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            fecha = None
    return panel, fecha


def leer_ratios(con) -> dict[str, dict]:
    """Filas de ratios_cedear por ticker_byma ({} si la tabla no existe
    todavia: la comparacion contra NY queda `sin_ratio`, no rompe la Fase 1)."""
    try:
        filas = con.execute(
            "SELECT ticker_byma, ticker_origen, ratio_cedear, ratio_subyacente, ratio, fecha_lista, tipo "
            "FROM ratios_cedear;"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {f[0]: {"ticker_origen": f[1], "ratio_cedear": f[2], "ratio_subyacente": f[3], "ratio": f[4],
                   "fecha_lista": f[5], "tipo": f[6]} for f in filas}


_CACHE_YF: dict[str, tuple[float, float | None]] = {}
CACHE_YF_SEG = 60
# Yahoo a veces se cuelga (medido 2026-09-25: 45 s y sin precio para un ETF tras
# un rate limit). La llamada corre en un hilo aparte y se abandona a los
# TIMEOUT_YF_SEG: el ETF queda sin_precio_ny y no frena la tarjeta entera.
TIMEOUT_YF_SEG = 8
_POOL_YF = ThreadPoolExecutor(max_workers=2)


def _last_price_yf(ticker_origen: str) -> float | None:
    import yfinance as yf

    precio = float(yf.Ticker(ticker_origen.replace(".", "-")).fast_info["last_price"])
    return precio if precio > 0 else None


# --- Modo demo (paso 7b): foto del cierre en vez de data912/yfinance en vivo ---
# generar_demo.py guarda junto a demo.db los paneles del dia de corte con las
# puntas igualadas al ultimo operado (`c`), el MEP/CCL y los precios de NY de
# ese cierre. En modo demo la herramienta compara cierres y no sale a la red.
def ruta_cierre_demo():
    return configuracion.ruta_db().parent / "cierre_cedears.json"


def leer_cierre_demo() -> dict:
    import json
    return json.loads(ruta_cierre_demo().read_text(encoding="utf-8"))


def precio_etf_yfinance(ticker_origen: str) -> float | None:
    """Ultimo precio de un ETF en yfinance (~15 min de demora). None si Yahoo
    no responde en TIMEOUT_YF_SEG o falla. Cache en memoria de 60 s (tambien
    de los None) para que recargar la solapa no le pegue a Yahoo cada vez."""
    if configuracion.es_demo():
        return leer_cierre_demo()["etf"].get(ticker_origen)
    import time

    ahora = time.monotonic()
    if ticker_origen in _CACHE_YF and ahora - _CACHE_YF[ticker_origen][0] < CACHE_YF_SEG:
        return _CACHE_YF[ticker_origen][1]
    futuro = _POOL_YF.submit(_last_price_yf, ticker_origen)
    try:
        precio = futuro.result(timeout=TIMEOUT_YF_SEG)
    except TimeoutError:
        # Si Yahoo contesta tarde, el precio igual entra al cache para la
        # proxima carga (el hilo sigue corriendo; el pedido actual no lo
        # espera). El None se escribe ANTES de enganchar el callback para que
        # nunca pise un precio que llego tarde.
        _CACHE_YF[ticker_origen] = (ahora, None)

        def _guardar_tarde(f, t=ticker_origen):
            if f.exception() is None and f.result():
                _CACHE_YF[t] = (time.monotonic(), f.result())
        futuro.add_done_callback(_guardar_tarde)
        return None
    except Exception:  # red, rate limit, ticker inexistente
        precio = None
    _CACHE_YF[ticker_origen] = (ahora, precio)
    return precio


def obtener_precios_ny() -> dict[str, dict]:
    """{symbol: {"precio": c, "panel": ...}} de usa_stocks + usa_adrs."""
    with ThreadPoolExecutor(max_workers=len(PANELES_NY)) as pool:
        respuestas = list(pool.map(lambda panel: _get(f"{DATA912_BASE}/{panel}").json(), PANELES_NY))
    salida = {}
    for panel, items in zip(PANELES_NY, respuestas):
        for item in items:
            if item["symbol"] not in salida:
                salida[item["symbol"]] = {"precio": item.get("c") or None, "panel": panel}
    return salida


def comparar_con_ny(ask_ars: float | None, ask_usd: float | None, precio_ny: float | None,
                    ratio: float | None, mep: float, ccl: float) -> dict:
    """Funcion pura: primas contra NY y control de split."""
    base = {"estado_ny": None, "precio_ny": precio_ny, "ratio": ratio, "ccl": ccl,
            "precio_teorico_ars": None, "precio_teorico_usd": None, "ny_implicito_ars": None,
            "ny_implicito_usd": None, "prima_ars_pct": None, "prima_usd_pct": None,
            "ratio_implicito": None, "desvio_ratio_pct": None, "ccl_implicito": None}
    if not ratio or ratio <= 0:
        return {**base, "estado_ny": "sin_ratio"}
    if not precio_ny or precio_ny <= 0:
        return {**base, "estado_ny": "sin_precio_ny"}
    if not ccl or ccl <= 0 or not mep or mep <= 0:
        raise ValueError(f"CCL/MEP invalidos: {ccl!r} / {mep!r}")
    base["precio_teorico_ars"] = precio_ny * ccl / ratio
    base["precio_teorico_usd"] = precio_ny / ratio * (ccl / mep)
    if not ask_ars or ask_ars <= 0:
        return {**base, "estado_ny": "sin_punta"}
    ratio_impl = precio_ny * ccl / ask_ars
    desvio = ratio_impl / ratio - 1
    base.update(ratio_implicito=ratio_impl, desvio_ratio_pct=desvio * 100)
    if abs(desvio) > DESVIO_MAX_RATIO:
        # con un ratio sospechoso el CCL implicito es tan enganoso como la prima
        return {**base, "estado_ny": "ratio_desactualizado"}
    base["ccl_implicito"] = ask_ars * ratio / precio_ny
    base["ny_implicito_ars"] = ask_ars * ratio / ccl
    base["prima_ars_pct"] = (base["ny_implicito_ars"] / precio_ny - 1) * 100
    if ask_usd and ask_usd > 0:
        base["ny_implicito_usd"] = ask_usd * ratio * mep / ccl
        base["prima_usd_pct"] = (base["ny_implicito_usd"] / precio_ny - 1) * 100
    return {**base, "estado_ny": "ok"}


def _punta(item: dict | None) -> dict | None:
    if item is None:
        return None
    return {"symbol": item["symbol"], "bid": item.get("px_bid") or None, "ask": item.get("px_ask") or None,
            "q_bid": item.get("q_bid"), "q_ask": item.get("q_ask"), "ultimo": item.get("c") or None,
            "volumen": item.get("v")}


def evaluar_ticker(ticker: str, panel: dict[str, dict], mep: float,
                   umbral_solo_usd: float = UMBRAL_SOLO_USD_DEFAULT, *,
                   ratios: dict[str, dict] | None = None, precios_ny: dict[str, dict] | None = None,
                   ccl: float | None = None, precio_etf=None) -> dict:
    """Evaluacion de un CEDEAR contra paneles ya descargados. Con `ratios`
    (filas de ratios_cedear por ticker_byma), `precios_ny` y `ccl` agrega la
    comparacion contra NY en la clave "ny". `precio_etf(ticker_origen)` es el
    respaldo SOLO para filas tipo 'etf' que data912 no tiene (yfinance)."""
    t = ticker_byma(ticker)
    item_ars = panel.get(t)
    sim_d = especie_d(t, panel) if item_ars else None
    item_usd = panel.get(sim_d) if sim_d else None
    ars, usd = _punta(item_ars), _punta(item_usd)
    salida = {"ticker": t, "ticker_pedido": ticker, "especie_ars": t if item_ars else None,
              "especie_usd": sim_d, "ars": ars, "usd": usd, "mep": mep}
    if item_ars is None:
        salida.update(evaluar(None, None, mep, umbral_solo_usd), estado="no_cotiza")
    elif item_usd is None:
        salida.update(evaluar(None, None, mep, umbral_solo_usd), estado="sin_especie_d")
    else:
        salida.update(evaluar(ars["ask"], usd["ask"], mep, umbral_solo_usd))

    if ratios is not None and precios_ny is not None and ccl:
        fila = ratios.get(t)
        ny = precios_ny.get(fila["ticker_origen"]) if fila else None
        if ny is None and fila and fila.get("tipo") == "etf" and precio_etf is not None:
            p = precio_etf(fila["ticker_origen"])
            ny = {"precio": p, "panel": "yfinance"} if p else None
        comp = comparar_con_ny(ars["ask"] if ars else None, usd["ask"] if usd else None,
                               ny["precio"] if ny else None, fila["ratio"] if fila else None, mep, ccl)
        comp.update(
            ticker_origen=fila["ticker_origen"] if fila else None,
            ratio_texto=f"{fila['ratio_cedear']:g}:{fila['ratio_subyacente']:g}" if fila else None,
            fecha_lista=fila["fecha_lista"] if fila else None,
            panel_ny=ny["panel"] if ny else None,
        )
        salida["ny"] = comp
    return salida


# Horario de rueda de BYMA (hora argentina). Fuera de este horario las puntas
# que quedan en el libro son ordenes viejas/lejanas: medido el 2026-09-24, a
# las 15:42 el control de split daba 0 casos y a las 17:47 (rueda cerrada) 8,
# todos por puntas absurdas (GFI $100.000 contra ~$64.000 teorico). No cubre
# feriados.
HORA_APERTURA, HORA_CIERRE = 11, 17


def fuera_de_horario(iso: str | None) -> bool | None:
    """True si la hora del dato (ISO con offset) cae fuera de la rueda de BYMA
    o en fin de semana; None si no hay hora."""
    if not iso:
        return None
    dt = datetime.fromisoformat(iso).astimezone(TZ_AR)
    return dt.weekday() >= 5 or not (HORA_APERTURA <= dt.hour < HORA_CIERRE)


def ahora_ar() -> str:
    return datetime.now(TZ_AR).isoformat(timespec="seconds")
