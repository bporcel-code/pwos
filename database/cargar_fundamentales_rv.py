"""
PWOS -- Carga de fundamentales de Renta Variable desde yfinance (Epica 19,
Fase 3, 2026-09-21). Puebla `fundamentales_rv` (v2.33): UNA fila por
ticker, la ultima carga.

Universo: las posiciones abiertas de RV de la cartera + las candidatas de
`watchlist_rv` (si una candidata ya esta en cartera, gana la cartera).

REGLA DE ORO: nada se inventa ni se rellena. Un campo que
no se puede calcular con datos confiables queda NULL y su motivo va a
`campos_no_verificados` como {campo: {"codigo": ..., "detalle": ...}}.
Codigos (los usa screener_rv.py):
  sin_dato       Yahoo no informa el dato
  inconsistente  el dato de Yahoo no coincide en orden de magnitud con los
                 estados financieros (tipico de un desajuste de moneda)
  moneda         moneda sin conversion soportada / cotizacion y estados en
                 monedas distintas sin forma de verificarlo
  no_aplica      el dato no aplica (EPS <= 0 -> P/E, EBITDA <= 0, ETF...)
  sin_intereses  la empresa no tiene gasto por intereses (cobertura no
                 acotada) -- es un dato, no una falta de dato

GUARDA DE MONEDA: una accion local puede cotizar en ARS y reportar en USD
(PAMP) o en pesos ajustados por inflacion (IRSA, LOMA). Todo lo monetario
se lleva a USD: USD -> tal cual; ARS -> / MEP del dia (ADR-001; el MEP sale
de `precios`); cualquier otra moneda -> sin verificar. Ademas, cada dato
monetario que informa Yahoo (EPS, deuda, EBITDA, FCF, capitalizacion) se
CONTRASTA contra los estados financieros propios (utilidad, deuda, EBITDA y
FCF anuales, precio x acciones): si el cociente cae fuera de [0,25 ; 4] se
descarta como "inconsistente" -- un desajuste de moneda seria del orden de
la cotizacion del dolar (~1.500), no de 4. Sin estados para contrastar, se
acepta el dato SOLO si cotizacion y estados estan en la misma moneda.

Unidades: `roic` y `fcf_yield` en PORCENTAJE (18,5 = 18,5 %); `eps_ttm` y
`bvps` en USD por accion; `per`/`pb`/`per_forward`/`current_ratio`/
`deuda_neta_ebitda`/`cobertura_intereses` son razones.
  - bvps = patrimonio de los estados / acciones (propio, no el `bookValue`
    de Yahoo); si faltan estados se usa el de Yahoo solo con monedas iguales.
  - roic = EBIT x (1 - tasa de impuesto) / capital invertido, del ultimo
    anual. La tasa sale de "Tax Rate For Calcs" o de impuesto/utilidad
    antes de impuestos; sin ninguna de las dos, sin verificar (no se asume).
  - fcf_yield = FCF / capitalizacion; el FCF sale de Yahoo (TTM) o, si Yahoo
    no lo informa, del ultimo estado anual (se anota en `fuente`).
  - anios_dividendo_sin_recorte: anios calendario COMPLETOS consecutivos,
    hacia atras desde el ultimo, sin un recorte. Recorte = el total anual Y
    el promedio por pago caen ambos mas de RECORTE_RELEVANTE_PCT (borrador a
    calibrar) -- ver anios_sin_recorte() para el porque de exigir las dos.
    Es un PISO: un ajuste de split mal aplicado en el historial viejo de
    Yahoo puede cortar la racha antes de tiempo (PEP), y el corte queda
    registrado en series_json para revisarlo. 0 = no paga dividendos.
  - Solo hay 4-5 anios de estados anuales en Yahoo: las reglas de 10 anios
    (ganancias, EPS creciente, P/E vs su promedio) NO son verificables.

SECTOR: Yahoo (traducido al español) salvo lo que el usuario fije a mano en
SECTOR_MANUAL, que manda siempre (mismo patron que MONEDA_PAGO_BONOS en
cargar_bonos_referencia_flujo.py). Sirve para las acciones locales que
Yahoo clasifica mal.

Uso:
    python cargar_fundamentales_rv.py --dry-run                # muestra, NO escribe
    python cargar_fundamentales_rv.py --dry-run --extra BKNG ZTS YPFD.BA
    python cargar_fundamentales_rv.py                          # carga real (backup previo)
    python cargar_fundamentales_rv.py --ticker BKNG            # solo uno (sin backup)
    python cargar_fundamentales_rv.py --dry-run --detalle      # ademas, los motivos
"""
import argparse
import json
import logging
import math
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

import yfinance as yf

from clasificar_yfinance import simbolo_yfinance, validar_nombre
from dashboard import calcular_ganancia_por_activo, obtener_precio_fx
from fetch_yfinance import fetch_yfinance_info
import configuracion

logger = logging.getLogger(__name__)

# En el contenedor pwos-api, database/pwos.db es el bind mount a V: y tiene
# datos; en la PC del usuario es un stub de 0 bytes -> se usa V: directo.
DB_PATH = configuracion.ruta_db()

FUENTE = "yfinance"
PAUSA_ENTRE_TICKERS_SEG = 1.0
RECORTE_RELEVANTE_PCT = 10          # borrador, a calibrar
MAX_ANIOS_SERIE_DIVIDENDOS = 30
RANGO_CONSISTENCIA = (0.25, 4.0)    # ver docstring (guarda de moneda)
PB_PLAUSIBLE = (0.01, 300.0)        # fuera de esto = estados y precio en distinta escala
PER_PLAUSIBLE = (0.1, 1000.0)
FCF_YIELD_MAX_PLAUSIBLE = 100.0     # % en valor absoluto
MIN_CAMPOS_INFO = 10                # un 404 real devuelve ~1 campo

# Sector a mano por ticker de `activos`/watchlist (manda sobre Yahoo). Ej.:
#   SECTOR_MANUAL = {"PAMP": "Energía"}
SECTOR_MANUAL: dict[str, str] = {}

SECTORES_ES = {
    "Technology": "Tecnología",
    "Healthcare": "Salud",
    "Financial Services": "Servicios financieros",
    "Consumer Cyclical": "Consumo cíclico",
    "Consumer Defensive": "Consumo defensivo",
    "Communication Services": "Servicios de comunicación",
    "Industrials": "Industriales",
    "Energy": "Energía",
    "Basic Materials": "Materiales básicos",
    "Real Estate": "Inmobiliario",
    "Utilities": "Servicios públicos",
}

COLUMNAS_DATO = (
    "simbolo_yahoo", "nombre", "tipo_instrumento", "fecha_dato", "fuente", "sector", "industria",
    "moneda_cotizacion", "moneda_estados", "precio_subyacente_usd", "per", "per_forward", "pb",
    "current_ratio", "deuda_neta_ebitda", "cobertura_intereses", "roic", "fcf_yield", "eps_ttm", "bvps",
    "anios_dividendo_sin_recorte", "anios_estados", "series_json", "campos_no_verificados",
)


# ---------------------------------------------------------------------------
# Calculo (puro: recibe los datos crudos ya descargados, no toca red ni base)
# ---------------------------------------------------------------------------

def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _primero(d: dict | None, *claves) -> float | None:
    for c in claves:
        v = _num((d or {}).get(c))
        if v is not None:
            return v
    return None


def a_usd(monto: float | None, moneda: str | None, mep: float | None) -> tuple[float | None, dict | None]:
    """(valor en USD, motivo). USD tal cual; ARS / MEP; otra moneda, no soportada."""
    if monto is None:
        return None, {"codigo": "sin_dato", "detalle": "sin dato"}
    if moneda == "USD":
        return monto, None
    if moneda == "ARS":
        if not mep:
            return None, {"codigo": "moneda", "detalle": "sin MEP para convertir ARS a USD"}
        return monto / mep, None
    return None, {"codigo": "moneda", "detalle": f"moneda {moneda!r} sin conversión soportada"}


def _factor_px_a_est(mon_px: str | None, mon_est: str | None, mep: float | None) -> float | None:
    """Multiplicador para llevar un monto de la moneda de COTIZACION a la de los
    ESTADOS (solo USD <-> ARS). None si son iguales o no hay conversion soportada."""
    if not mep or not mon_px or not mon_est or mon_px == mon_est:
        return None
    if mon_px == "ARS" and mon_est == "USD":
        return 1 / mep
    if mon_px == "USD" and mon_est == "ARS":
        return mep
    return None


def _validar_contra_estados(campo: str, valor_yahoo: float | None, valor_estados: float | None,
                            misma_moneda: bool, factor_alt: float | None = None
                            ) -> tuple[float | None, dict | None, str | None]:
    """Contrasta un dato monetario de Yahoo contra el equivalente de los estados.
    Devuelve (valor en la MONEDA DE LOS ESTADOS, motivo, nota).

    Yahoo no documenta en que moneda informa cada campo cuando la accion cotiza
    en una moneda y reporta en otra (PAMP: el EPS viene en pesos, no en dolares).
    En vez de suponerlo, se prueba la hipotesis "en la moneda de los estados" y,
    si no coincide, la hipotesis "en la moneda de cotizacion" (`factor_alt`): se
    acepta la que concuerda con los estados y la nota lo deja registrado."""
    if valor_yahoo is None:
        return None, {"codigo": "sin_dato", "detalle": f"Yahoo no informa {campo}"}, None
    if valor_estados not in (None, 0):
        lo, hi = RANGO_CONSISTENCIA
        if lo <= valor_yahoo / valor_estados <= hi:
            return valor_yahoo, None, None
        if factor_alt:
            alt = valor_yahoo * factor_alt
            if lo <= alt / valor_estados <= hi:
                return alt, None, f"{campo}: Yahoo lo informa en la moneda de cotización (convertido)"
        return None, {"codigo": "inconsistente",
                      "detalle": f"{campo} de Yahoo ({valor_yahoo:,.4g}) no coincide con los estados ({valor_estados:,.4g}): posible desajuste de moneda"}, None
    if misma_moneda:
        return valor_yahoo, None, None
    return None, {"codigo": "moneda", "detalle": f"{campo}: sin estados para contrastar y cotización ≠ estados"}, None


def anios_sin_recorte(div_por_anio: dict[int, dict], hoy: date) -> tuple[int, dict | None]:
    """(anios, corte). `div_por_anio` = {año: {"total": suma del año, "pagos": cantidad}}.

    Un año es RECORTE respecto del anterior solo si el TOTAL anual Y el PROMEDIO
    POR PAGO caen ambos mas de RECORTE_RELEVANTE_PCT. Ninguno de los dos solo
    sirve: el total solo confunde un año de 5 pagos con uno de 4 (PG 2003 -24 %,
    ADP 2002 -12 %, sin recorte real: el promedio por pago no cayo), y el promedio
    solo confunde un cambio de un pago anual a trimestrales (MCD 2007 -> 2008:
    total +8 %, promedio -73 %). Un recorte real baja los dos a la vez (PFE 2009).

    `corte` = {"anio", "caida_pct", "caida_por_pago_pct"} del año donde se corto la
    racha (None si llega hasta donde hay historia): un corte enorme (PEP 1998,
    -86 %) es casi seguro un ajuste de split mal aplicado en el historial viejo de
    Yahoo, y asi queda a la vista para revisarlo en vez de leerlo como un hecho.
    0 = sin dividendos (o dejo de pagar)."""
    completos = {y: (v["total"], v["total"] / v["pagos"]) for y, v in div_por_anio.items()
                 if y < hoy.year and v.get("pagos") and v["total"] > 0}
    if not completos or (hoy.year - 1) not in completos:
        return 0, None
    umbral = 1 - RECORTE_RELEVANTE_PCT / 100
    y, n = hoy.year - 1, 1
    while (y - 1) in completos:
        (tot, prom), (tot_ant, prom_ant) = completos[y], completos[y - 1]
        if tot < tot_ant * umbral and prom < prom_ant * umbral:
            return n, {"anio": y, "caida_pct": round((tot / tot_ant - 1) * 100, 1),
                       "caida_por_pago_pct": round((prom / prom_ant - 1) * 100, 1)}
        n += 1
        y -= 1
    return n, None


def calcular_fila(ticker: str, simbolo: str, crudo: dict, mep: float | None, hoy: date | None = None) -> tuple[dict, dict]:
    """(fila para fundamentales_rv, campos_no_verificados)."""
    hoy = hoy or date.today()
    info = crudo.get("info") or {}
    income, balance, cashflow = crudo.get("income") or {}, crudo.get("balance") or {}, crudo.get("cashflow") or {}
    nv: dict[str, dict] = {}
    fila = {c: None for c in COLUMNAS_DATO}
    fila.update(simbolo_yahoo=simbolo, fecha_dato=hoy.isoformat(), fuente=FUENTE)

    def falta(campo: str, motivo: dict | None) -> None:
        if motivo:
            nv[campo] = motivo

    if len(info) < MIN_CAMPOS_INFO:
        nv["_todo"] = {"codigo": "sin_dato", "detalle": "Yahoo no resolvió el símbolo (sin cobertura)"}
        return fila, nv

    nombre = info.get("longName") or info.get("shortName")
    fila["nombre"] = nombre
    if not validar_nombre(ticker, nombre):
        nv["_todo"] = {"codigo": "inconsistente",
                       "detalle": f"el nombre que devuelve Yahoo ({nombre!r}) no coincide con el esperado: posible listado equivocado"}
        return fila, nv

    qt = str(info.get("quoteType") or "").upper()
    fila["tipo_instrumento"] = "empresa" if qt == "EQUITY" else "etf" if qt in ("ETF", "MUTUALFUND") else "otro" if qt else None
    if fila["tipo_instrumento"] == "etf":
        nv["_todo"] = {"codigo": "no_aplica", "detalle": "ETF: no tiene fundamentales de empresa"}
        return fila, nv

    # sector: manual > Yahoo (traducido)
    fila["industria"] = info.get("industry")
    sector_yahoo = info.get("sector")
    if ticker in SECTOR_MANUAL:
        fila["sector"] = SECTOR_MANUAL[ticker]
    elif sector_yahoo in SECTORES_ES:
        fila["sector"] = SECTORES_ES[sector_yahoo]
    else:
        falta("sector", {"codigo": "sin_dato", "detalle": f"Yahoo no clasifica el sector ({sector_yahoo!r}); cargarlo en SECTOR_MANUAL"})

    mon_px = info.get("currency")
    mon_est = info.get("financialCurrency")
    fila["moneda_cotizacion"], fila["moneda_estados"] = mon_px, mon_est
    misma_moneda = bool(mon_px) and mon_px == mon_est

    ya = max(income) if income else None
    yb = max(balance) if balance else None
    yc = max(cashflow) if cashflow else None
    inc = income.get(ya, {}) if ya else {}
    bal = balance.get(yb, {}) if yb else {}
    cfs = cashflow.get(yc, {}) if yc else {}
    acciones = _primero(info, "sharesOutstanding", "impliedSharesOutstanding")
    factor_alt = _factor_px_a_est(mon_px, mon_est, mep)
    notas: list[str] = []

    # precio
    precio = _primero(info, "currentPrice", "regularMarketPrice", "previousClose")
    fila["precio_subyacente_usd"], m = a_usd(precio, mon_px, mep)
    falta("precio_subyacente_usd", m)
    precio_usd = fila["precio_subyacente_usd"]

    # EPS (USD por accion), contrastado con utilidad neta / acciones
    ni = _primero(inc, "Net Income", "Net Income Common Stockholders")
    eps_propio = ni / acciones if ni is not None and acciones else None
    eps_yahoo, m, nota = _validar_contra_estados("trailingEps", _num(info.get("trailingEps")), eps_propio, misma_moneda, factor_alt)
    falta("eps_ttm", m)
    if nota:
        notas.append(nota)
    if eps_yahoo is not None:
        fila["eps_ttm"], m = a_usd(eps_yahoo, mon_est, mep)
        falta("eps_ttm", m)

    # BVPS (USD por accion): patrimonio de los estados / acciones
    patrimonio = _primero(bal, "Common Stock Equity", "Stockholders Equity")
    bv_local = patrimonio / acciones if patrimonio is not None and acciones else None
    if bv_local is None and misma_moneda:
        bv_local = _num(info.get("bookValue"))
    if bv_local is None:
        falta("bvps", {"codigo": "sin_dato", "detalle": "sin patrimonio/acciones en los estados ni bookValue con monedas iguales"})
    else:
        fila["bvps"], m = a_usd(bv_local, mon_est, mep)
        falta("bvps", m)

    # P/E y P/B (ambos en USD)
    eps, bv = fila["eps_ttm"], fila["bvps"]
    if precio_usd is None or eps is None:
        falta("per", nv.get("eps_ttm") or nv.get("precio_subyacente_usd") or {"codigo": "sin_dato", "detalle": "sin precio o EPS verificados"})
    elif eps <= 0:
        falta("per", {"codigo": "no_aplica", "detalle": "EPS ≤ 0 (pérdidas): P/E no aplica"})
    else:
        fila["per"] = precio_usd / eps
    if precio_usd is None or bv is None:
        falta("pb", nv.get("bvps") or nv.get("precio_subyacente_usd") or {"codigo": "sin_dato", "detalle": "sin precio o patrimonio verificados"})
    elif bv <= 0:
        falta("pb", {"codigo": "no_aplica", "detalle": "patrimonio ≤ 0: P/B no aplica"})
    else:
        fila["pb"] = precio_usd / bv

    # Plausibilidad: un desajuste de escala entre el precio y los estados (Yahoo
    # etiqueta como pesos estados que estan en dolares, YPFD) multiplica o divide
    # las razones por ~el MEP: P/B de 3.000 en vez de 2. Se descarta, no se muestra.
    # `escala_ok` True = al menos una razon plausible y ninguna absurda -> los
    # estados estan en la misma escala que el precio (habilita el FCF anual mas abajo).
    escala_ok: bool | None = None
    for razon, (lo, hi), descartar in (("pb", PB_PLAUSIBLE, ("pb", "bvps")), ("per", PER_PLAUSIBLE, ("per", "eps_ttm"))):
        v = fila[razon]
        if v is None:
            continue
        if lo <= v <= hi:
            escala_ok = False if escala_ok is False else True
        else:
            for campo in descartar:
                fila[campo] = None
                falta(campo, {"codigo": "inconsistente",
                              "detalle": f"{razon.upper()} de {v:,.4g} es implausible: estados y precio en distinta escala o moneda mal etiquetada"})
            escala_ok = False

    # P/E forward: Yahoo lo arma con su propia moneda de estimacion -> solo con monedas iguales
    fpe = _num(info.get("forwardPE"))
    if fpe is None:
        falta("per_forward", {"codigo": "sin_dato", "detalle": "Yahoo no informa forwardPE"})
    elif not misma_moneda:
        falta("per_forward", {"codigo": "moneda", "detalle": "forwardPE con cotización ≠ estados: moneda de la estimación no verificable"})
    elif fpe <= 0:
        falta("per_forward", {"codigo": "no_aplica", "detalle": "forwardPE ≤ 0: no aplica"})
    else:
        fila["per_forward"] = fpe

    # current ratio (razon, no depende de la moneda)
    cr = _num(info.get("currentRatio"))
    if cr is None:
        ca, cl = _primero(bal, "Current Assets"), _primero(bal, "Current Liabilities")
        cr = ca / cl if ca is not None and cl else None
    if cr is None:
        falta("current_ratio", {"codigo": "sin_dato", "detalle": "sin currentRatio ni activos/pasivos corrientes"})
    else:
        fila["current_ratio"] = cr

    # deuda neta / EBITDA (todo en moneda de los estados, contrastado contra los estados)
    deuda, m1, n1 = _validar_contra_estados("totalDebt", _num(info.get("totalDebt")), _primero(bal, "Total Debt"), misma_moneda, factor_alt)
    ebitda, m2, n2 = _validar_contra_estados("ebitda", _num(info.get("ebitda")), _primero(inc, "EBITDA", "Normalized EBITDA"), misma_moneda, factor_alt)
    notas.extend(n for n in (n1, n2) if n)
    caja = _num(info.get("totalCash"))
    if deuda is None or ebitda is None or caja is None:
        falta("deuda_neta_ebitda", m1 or m2 or {"codigo": "sin_dato", "detalle": "Yahoo no informa totalCash"})
    elif ebitda <= 0:
        falta("deuda_neta_ebitda", {"codigo": "no_aplica", "detalle": "EBITDA ≤ 0: deuda neta/EBITDA no aplica"})
    else:
        fila["deuda_neta_ebitda"] = (deuda - caja) / ebitda

    # cobertura de intereses (EBIT / gasto por intereses, ultimo anual)
    ebit = _primero(inc, "EBIT", "Operating Income")
    interes = _primero(inc, "Interest Expense", "Interest Expense Non Operating")
    if ebit is None or interes is None:
        falta("cobertura_intereses", {"codigo": "sin_dato", "detalle": "sin EBIT o gasto por intereses en los estados"})
    elif interes == 0:
        falta("cobertura_intereses", {"codigo": "sin_intereses", "detalle": "sin gasto por intereses: cobertura no acotada"})
    else:
        fila["cobertura_intereses"] = ebit / abs(interes)

    # ROIC (%)
    tasa = _primero(inc, "Tax Rate For Calcs")
    if tasa is None:
        prov, antes = _primero(inc, "Tax Provision"), _primero(inc, "Pretax Income")
        if prov is not None and antes and antes > 0 and 0 <= prov / antes <= 0.6:
            tasa = prov / antes
    capital = _primero(bal, "Invested Capital")
    if ebit is None or tasa is None or capital is None:
        falta("roic", {"codigo": "sin_dato", "detalle": "sin EBIT, tasa de impuesto o capital invertido (la tasa no se asume)"})
    elif capital <= 0:
        falta("roic", {"codigo": "no_aplica", "detalle": "capital invertido ≤ 0: ROIC no aplica"})
    else:
        fila["roic"] = ebit * (1 - tasa) / capital * 100

    # FCF yield (%)
    fcf_estado = _primero(cfs, "Free Cash Flow")
    fcf, m, nota = _validar_contra_estados("freeCashflow", _num(info.get("freeCashflow")), fcf_estado, misma_moneda, factor_alt)
    if nota:
        notas.append(nota)
    # El `freeCashflow` de Yahoo a veces es un trimestre, no el TTM (MSFT: 16,5 mil M
    # contra 67 mil M anuales). Si falta o no coincide con los estados se usa el FCF
    # ANUAL de los estados -- pero solo si la escala de los estados quedo verificada
    # (`escala_ok`): un FCF de estados mal etiquetados daria un yield chico y falso.
    if fcf is None and m and m["codigo"] in ("sin_dato", "inconsistente") and fcf_estado is not None and escala_ok is True:
        fcf, m = fcf_estado, None
        notas.append("fcf: estado anual (el de Yahoo no está informado o no coincide con los estados)")
    mcap_local, m_cap, _ = _validar_contra_estados(
        "marketCap", _num(info.get("marketCap")), precio * acciones if precio and acciones else None, True)
    fcf_usd, m_f = a_usd(fcf, mon_est, mep)
    mcap_usd, m_c = a_usd(mcap_local, mon_px, mep)
    if fcf_usd is None or not mcap_usd:
        falta("fcf_yield", m or m_cap or m_f or m_c or {"codigo": "sin_dato", "detalle": "sin FCF o capitalización"})
    else:
        rendimiento = fcf_usd / mcap_usd * 100
        if abs(rendimiento) > FCF_YIELD_MAX_PLAUSIBLE:
            falta("fcf_yield", {"codigo": "inconsistente", "detalle": f"FCF yield de {rendimiento:,.4g} % es implausible"})
        else:
            fila["fcf_yield"] = rendimiento
    fila["fuente"] = FUENTE + "".join(f"; {n}" for n in notas)

    # dividendos y series crudas
    divs = {int(y): v for y, v in (crudo.get("dividendos") or {}).items()}
    fila["anios_dividendo_sin_recorte"], corte = anios_sin_recorte(divs, hoy)
    anual = {}
    for y in sorted(income)[-5:]:
        ing, res = _primero(income[y], "Total Revenue"), _primero(income[y], "EBIT", "Operating Income")
        anual[str(y)] = {
            "ingresos": ing,
            "utilidad_neta": _primero(income[y], "Net Income", "Net Income Common Stockholders"),
            "margen_operativo": res / ing * 100 if res is not None and ing else None,
            "fcf": _primero(cashflow.get(y, {}), "Free Cash Flow"),
        }
    fila["anios_estados"] = sum(1 for v in anual.values() if v["utilidad_neta"] is not None)
    ultimos = sorted(divs)[-MAX_ANIOS_SERIE_DIVIDENDOS:]
    fila["series_json"] = json.dumps({
        "moneda_estados": mon_est, "anual": anual,
        "dividendos": {str(y): {"total": round(divs[y]["total"], 6), "pagos": divs[y]["pagos"]} for y in ultimos},
        "racha_dividendos": {"anios": fila["anios_dividendo_sin_recorte"], "cortada_en": corte},
    })
    return fila, nv


# ---------------------------------------------------------------------------
# Red (yfinance)
# ---------------------------------------------------------------------------

def _df_a_dict(df) -> dict[int, dict[str, float]]:
    """DataFrame de yfinance (filas = concepto, columnas = cierre del ejercicio)
    -> {año: {concepto: valor}}, sin los NaN."""
    if df is None or getattr(df, "empty", True):
        return {}
    out: dict[int, dict[str, float]] = {}
    for col in df.columns:
        anio = col.year if hasattr(col, "year") else int(str(col)[:4])
        filas = {str(idx): _num(v) for idx, v in df[col].items() if _num(v) is not None}
        if filas:
            out[anio] = filas
    return out


def descargar(simbolo: str) -> dict:
    """Todo lo que necesita calcular_fila. Las excepciones de red se propagan."""
    info = fetch_yfinance_info(simbolo)
    if not info or len(info) < MIN_CAMPOS_INFO:
        return {"info": info or {}}
    t = yf.Ticker(simbolo)
    divs = t.dividends
    por_anio: dict[int, dict] = {}
    if divs is not None and len(divs):
        for fecha, monto in divs.items():
            d = por_anio.setdefault(fecha.year, {"total": 0.0, "pagos": 0})
            d["total"] += float(monto)
            d["pagos"] += 1
    return {"info": info, "income": _df_a_dict(t.income_stmt), "balance": _df_a_dict(t.balance_sheet),
            "cashflow": _df_a_dict(t.cashflow), "dividendos": por_anio}


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

def universo(con, mep: float, ccl: float, extra: list[str] | None = None) -> list[tuple[str, str, str]]:
    """[(ticker, simbolo_yahoo, origen)] con origen 'cartera' | 'watchlist' | 'extra'."""
    salida: list[tuple[str, str, str]] = []
    vistos: set[str] = set()
    for p in sorted(calcular_ganancia_por_activo(con, mep, ccl), key=lambda x: x["ticker"]):
        if p["tipo"] in ("accion", "cedear") and p["posicion_abierta"]:
            salida.append((p["ticker"], simbolo_yfinance(p["ticker"], p["tipo"]), "cartera"))
            vistos.add(p["ticker"])
    for (t,) in con.execute("SELECT ticker FROM watchlist_rv ORDER BY ticker;"):
        if t not in vistos:
            salida.append((t, t, "watchlist"))
            vistos.add(t)
    for t in extra or []:
        t = t.strip().upper()
        if t not in vistos:
            salida.append((t, t, "extra"))
            vistos.add(t)
    return salida


def hacer_backup(db_path: Path) -> Path:
    destino = db_path.parent / "backups" / f"{db_path.name}.backup_fundamentales_rv_{datetime.now():%Y-%m-%d_%H%M%S}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, destino)
    c = sqlite3.connect(destino)
    ok = c.execute("PRAGMA integrity_check;").fetchone()[0]
    c.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {destino.name} (integrity_check=ok)")
    return destino


def _falla_de_cobertura(nv: dict) -> bool:
    """Yahoo no devolvio datos utilizables: no resolvio el simbolo o devolvio otra
    empresa. NO incluye 'no_aplica' (un ETF es un resultado legitimo)."""
    return nv.get("_todo", {}).get("codigo") in ("sin_dato", "inconsistente")


def _tiene_datos_previos(con, ticker: str) -> bool:
    fila = con.execute("SELECT tipo_instrumento FROM fundamentales_rv WHERE ticker = ?;", (ticker,)).fetchone()
    return fila is not None and fila[0] is not None


def guardar(con, ticker: str, fila: dict, nv: dict) -> None:
    valores = dict(fila, campos_no_verificados=json.dumps(nv, ensure_ascii=False) if nv else None)
    cols = ("ticker",) + COLUMNAS_DATO
    sets = ", ".join(f"{c} = excluded.{c}" for c in COLUMNAS_DATO)
    con.execute(
        f"INSERT INTO fundamentales_rv ({', '.join(cols)}, actualizado_en) "
        f"VALUES ({', '.join('?' * len(cols))}, CURRENT_TIMESTAMP) "
        f"ON CONFLICT (ticker) DO UPDATE SET {sets}, actualizado_en = CURRENT_TIMESTAMP;",
        (ticker, *(valores[c] for c in COLUMNAS_DATO)),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Salida y main
# ---------------------------------------------------------------------------

def _f(v, dec=1, suf="") -> str:
    return "—" if v is None else f"{v:,.{dec}f}{suf}"


def imprimir_tabla(filas: list[tuple[str, str, str, dict, dict]]) -> None:
    print(f"\n{'ticker':8} {'origen':9} {'tipo':8} {'sector':24} {'mon px/est':10} {'USD':>9} {'PER':>6} {'PB':>6} "
          f"{'ROIC%':>6} {'FCF%':>6} {'ND/EBITDA':>9} {'cobert':>7} {'CR':>5} {'div':>4} {'años':>4} {'s/v':>4}")
    for ticker, _, origen, fila, nv in filas:
        campos_sv = [k for k in nv if not k.startswith("_")]
        print(f"{ticker:8} {origen:9} {str(fila['tipo_instrumento'] or '—'):8} {str(fila['sector'] or '—')[:24]:24} "
              f"{str(fila['moneda_cotizacion'] or '—') + '/' + str(fila['moneda_estados'] or '—'):10} "
              f"{_f(fila['precio_subyacente_usd'], 2):>9} {_f(fila['per']):>6} {_f(fila['pb'], 2):>6} {_f(fila['roic']):>6} "
              f"{_f(fila['fcf_yield']):>6} {_f(fila['deuda_neta_ebitda'], 2):>9} {_f(fila['cobertura_intereses']):>7} "
              f"{_f(fila['current_ratio'], 2):>5} {fila['anios_dividendo_sin_recorte'] if fila['anios_dividendo_sin_recorte'] is not None else '—':>4} "
              f"{fila['anios_estados'] if fila['anios_estados'] is not None else '—':>4} {len(campos_sv):>4}"
              + ("  <- " + nv["_todo"]["detalle"] if "_todo" in nv else ""))


def main() -> int:
    # Los motivos llevan caracteres como "≠" o "≤": en una consola cp1252 un print
    # fallaria a mitad de una carga. Se reemplaza lo no representable, no se aborta.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="PWOS - fundamentales de Renta Variable (yfinance)")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="descarga y muestra, NO escribe nada")
    ap.add_argument("--ticker", help="cargar solo este ticker (simbolo de Yahoo -- no hace falta que ya este en la cartera ni en la watchlist, ver simulador_rv.py)")
    ap.add_argument("--extra", nargs="*", default=[], help="simbolos de Yahoo extra (solo con --dry-run)")
    ap.add_argument("--detalle", action="store_true", help="imprime el motivo de cada campo sin verificar")
    args = ap.parse_args()
    if args.extra and not args.dry_run:
        ap.error("--extra solo se puede usar con --dry-run (las candidatas reales van a la watchlist)")
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    uri = f"file:{args.db.as_posix()}?mode=ro" if args.dry_run else str(args.db)
    con = sqlite3.connect(uri, uri=args.dry_run)
    mep, ccl = obtener_precio_fx(con, "USD_MEP"), obtener_precio_fx(con, "USD_CCL")
    lista = universo(con, mep, ccl, args.extra)
    if args.ticker:
        t = args.ticker.strip().upper()
        # Ad-hoc: si el ticker YA esta en la cartera/watchlist se reusa su
        # entrada (conserva el origen real); si no, se procesa igual -- el
        # Simulador de compra (simulador_rv.py) necesita poder traer datos
        # de un candidato que todavia no se agrego a ningun lado (agregar a
        # la watchlist es un boton aparte, posterior a simular).
        lista = [x for x in lista if x[0] == t] or [(t, t, "ticker")]
    print(f"Universo: {len(lista)} tickers | MEP {mep:,.2f} | {'DRY-RUN (no escribe)' if args.dry_run else 'carga real'} | base: {args.db}")

    if not args.dry_run and not args.ticker:
        hacer_backup(args.db)

    resultados, errores, conservadas = [], [], []
    for i, (ticker, simbolo, origen) in enumerate(lista):
        try:
            crudo = descargar(simbolo)
            fila, nv = calcular_fila(ticker, simbolo, crudo, mep)
        except Exception as e:  # una falla en un ticker no frena a los demas
            errores.append((ticker, str(e)[:120]))
            print(f"  ERROR {ticker} ({simbolo}): {str(e)[:120]} -- se conserva la fila anterior, si la hay")
            continue
        resultados.append((ticker, simbolo, origen, fila, nv))
        if not args.dry_run and origen != "extra":
            # Yahoo devuelve a veces respuestas vacias o parciales: una falla de
            # cobertura NUNCA pisa una fila que ya tiene datos (solo se escribe la
            # fila vacia la primera vez, para que la UI sepa que se intento).
            if _falla_de_cobertura(nv) and _tiene_datos_previos(con, ticker):
                conservadas.append(ticker)
                print(f"  AVISO {ticker}: Yahoo no devolvió datos utilizables ({nv['_todo']['detalle']}) -- se CONSERVA la fila anterior")
            else:
                guardar(con, ticker, fila, nv)
        if i < len(lista) - 1:
            time.sleep(PAUSA_ENTRE_TICKERS_SEG)

    imprimir_tabla(resultados)
    if args.detalle:
        print("\nCampos sin verificar (codigo: detalle):")
        for ticker, _, _, _, nv in resultados:
            for campo, m in nv.items():
                print(f"  {ticker:8} {campo:22} {m['codigo']:13} {m['detalle']}")
    escritas = sum(1 for r in resultados if r[2] != "extra" and r[0] not in conservadas)
    print(f"\nOK: {len(resultados)} | errores: {len(errores)}"
          + ("" if args.dry_run else f" | escritos en fundamentales_rv: {escritas} | filas anteriores conservadas: {len(conservadas)}"))
    con.close()
    return 0 if resultados or not lista else 1


if __name__ == "__main__":
    sys.exit(main())
