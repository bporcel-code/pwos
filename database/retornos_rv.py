"""
PWOS -- Retornos semanales, correlacion y descomposicion de riesgo de
Renta Variable (Epica 19, Fase 4, 2026-09-22). Modulo PURO: solo lee de la
base, nunca toca la red -- lo usan tanto el panel "Correlacion y
contribucion al riesgo" (correlacion_rv.py) como el Simulador de compra
(simulador_rv.py), que le agrega una columna mas (el candidato).

Herramienta de DIAGNOSTICO, no un oraculo: informa, no decide (mismo
criterio que el resto de la solapa).

Base de retornos (decision del usuario, EXCEPCION documentada a ADR-001):
  - accion local, de CARTERA (tuvo o tiene una fila en `activos`): USD via
    MEP HISTORICO de la fecha EXACTA (`precios`, backfill desde 2023) --
    nunca se aproxima con el dia mas cercano.
  - cedear (cartera) o CUALQUIER candidata de la watchlist: USD del
    SUBYACENTE, ya cacheado en `precios_candidatas` por cargar_precios_rv.py
    (precio AJUSTADO por dividendos -- ver docstring de ese script para el
    porque, distinto del precio crudo que usa el resto del proyecto).
Frecuencia SEMANAL en todo (decision del usuario): se resamplea al ultimo
precio de cada semana (viernes) y se calcula el retorno semana a semana --
mezclar frecuencias entre tickers invalidaria la matriz de covarianza.

Dos umbrales de confianza, no uno solo:
  - MIN_SEMANAS_MINIMO: piso absoluto para que un ticker entre al calculo.
    Por debajo, no hay base estadistica ninguna -- queda afuera y listado
    aparte (mismo patron que el `MIN_OBSERVACIONES` original).
  - MIN_MESES_ALTA_CONFIANZA (36, pedido del ticket): con menos de esta
    ventana el ticker SI entra al calculo, pero se marca "confianza baja"
    -- una posicion recien comprada no debe desaparecer del mapa, solo
    advertir que el numero es menos solido.

Contribucion al riesgo: descomposicion de Euler estandar de Markowitz
(RC_i = w_i x (Sigma w)_i / vol_cartera, suma exacto a vol_cartera) -- no
es una formula inventada, es el metodo de libro de texto para risk
budgeting. "Diversificacion efectiva" = numero efectivo de apuestas
(1 / suma de contribuciones^2, Herfindahl invertido sobre las
contribuciones al riesgo, no sobre los pesos crudos -- asi si baja
porque 2 posiciones estan MUY correlacionadas, aunque sean pesos chicos
por separado).

La covarianza usa observaciones PAIRWISE-COMPLETE (mismo criterio que ya
usaba pandas.corr() en la version anterior de este modulo): un par de
tickers con menos de MIN_SEMANAS_MINIMO semanas en comun queda con
covarianza 0 entre si (no se puede estimar, se asume sin relacion en vez
de romper el calculo) -- valido para un panel de diagnostico, NO para
optimizar una cartera (eso, si llega, es la Fase 5).
"""
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import configuracion

MIN_SEMANAS_MINIMO = 12          # ~3 meses; piso para entrar al calculo
MIN_MESES_ALTA_CONFIANZA = 36    # pedido del ticket; por debajo, "confianza_baja"
UMBRAL_REDUNDANCIA = 0.85
RESAMPLE_SEMANAL = "W-FRI"
SEMANAS_POR_ANIO = 52


_SERIE_VACIA = pd.Series(dtype=float, index=pd.DatetimeIndex([]))  # nunca un RangeIndex por defecto:
# un ticker sin ninguna fila (recien agregado, todavia sin pasar por el
# loader) es un caso real y esperado, no una excepcion -- .resample() mas
# abajo exige un DatetimeIndex incluso en la serie vacia, si no explota.


def _precios_diarios_accion_local(con, ticker: str) -> pd.Series:
    """USD, de `precios` (ARS) / MEP HISTORICO de la fecha exacta. Solo para
    tickers que tienen fila en `activos` (posicion actual o pasada)."""
    filas = con.execute(
        """
        SELECT p.fecha, p.precio / mep.precio
        FROM precios p
        JOIN activos a ON a.id = p.activo_id AND a.ticker = ?
        JOIN precios mep ON mep.fecha = p.fecha
        JOIN activos amep ON amep.id = mep.activo_id AND amep.ticker = 'USD_MEP'
        ORDER BY p.fecha;
        """,
        (ticker,),
    ).fetchall()
    if not filas:
        return _SERIE_VACIA
    fechas, precios = zip(*filas)
    return pd.Series(precios, index=pd.to_datetime(fechas), dtype=float)


def _precios_diarios_candidatas(con, ticker: str) -> pd.Series:
    """USD ya ajustado, de `precios_candidatas` (cargar_precios_rv.py) --
    CEDEARs de cartera (subyacente) y cualquier candidata de la watchlist."""
    filas = con.execute(
        "SELECT fecha, cierre_usd FROM precios_candidatas WHERE ticker = ? ORDER BY fecha;", (ticker,)
    ).fetchall()
    if not filas:
        return _SERIE_VACIA
    fechas, precios = zip(*filas)
    return pd.Series(precios, index=pd.to_datetime(fechas), dtype=float)


def serie_diaria_usd(con, ticker: str, tipo: str) -> pd.Series:
    """`tipo`: 'accion' (cartera, via `precios`+MEP) o 'cedear'/'candidata'
    (via `precios_candidatas`)."""
    return _precios_diarios_accion_local(con, ticker) if tipo == "accion" else _precios_diarios_candidatas(con, ticker)


def ultimo_viernes_completo(hasta) -> pd.Timestamp:
    """Ultimo viernes ESTRICTAMENTE anterior a `hasta` (semana cerrada).
    Unica definicion de "semana completa" del proyecto (la reusa
    frontera_rv.py)."""
    d = pd.Timestamp(hasta).normalize() - pd.Timedelta(days=1)
    while d.weekday() != 4:
        d -= pd.Timedelta(days=1)
    return d


def _retornos_semanales(precios_diarios: pd.Series, hasta=None) -> pd.Series:
    """Retornos semanales SOLO de semanas cerradas. Fix 2026-09-23 (ver
    ESTADO_PWOS.md): `resample("W-FRI")` rotula la semana en curso con el
    viernes FUTURO, asi que el ultimo retorno era parcial (p.ej. 09-25 el
    dia 09-23) y entraba en correlacion/volatilidad como si fuera una
    semana entera. `hasta` (default: hoy) fija el corte."""
    semanal = precios_diarios.resample(RESAMPLE_SEMANAL).last().dropna()
    semanal = semanal[semanal.index <= ultimo_viernes_completo(hasta if hasta is not None else configuracion.hoy())]
    return semanal.pct_change().dropna()


def _cobertura(retornos: pd.Series) -> dict:
    n = len(retornos)
    if n == 0:
        return {"semanas": 0, "meses_span": 0.0, "desde": None, "hasta": None}
    dias_span = (retornos.index[-1] - retornos.index[0]).days
    return {
        "semanas": n,
        "meses_span": round(dias_span / 30.44, 1),
        "desde": retornos.index[0].strftime("%Y-%m-%d"),
        "hasta": retornos.index[-1].strftime("%Y-%m-%d"),
    }


@dataclass
class RetornosRV:
    retornos: pd.DataFrame              # semanas x tickers INCLUIDOS (>= MIN_SEMANAS_MINIMO)
    cobertura: dict[str, dict] = field(default_factory=dict)   # TODOS los pedidos, incluidos o no
    incluidos: list[str] = field(default_factory=list)
    excluidos: list[str] = field(default_factory=list)         # < MIN_SEMANAS_MINIMO
    confianza_baja: list[str] = field(default_factory=list)    # incluidos pero < MIN_MESES_ALTA_CONFIANZA


def construir(con, tickers: list[tuple[str, str]], hasta=None) -> RetornosRV:
    """`tickers`: [(ticker, tipo)], tipo en 'accion'/'cedear'/'candidata'."""
    series, cobertura = {}, {}
    for ticker, tipo in tickers:
        semanal = _retornos_semanales(serie_diaria_usd(con, ticker, tipo), hasta)
        series[ticker] = semanal
        cobertura[ticker] = _cobertura(semanal)

    incluidos = [t for t, c in cobertura.items() if c["semanas"] >= MIN_SEMANAS_MINIMO]
    excluidos = sorted(t for t in cobertura if t not in incluidos)
    confianza_baja = sorted(t for t in incluidos if cobertura[t]["meses_span"] < MIN_MESES_ALTA_CONFIANZA)

    df = pd.concat({t: series[t] for t in incluidos}, axis=1).sort_index() if incluidos else pd.DataFrame()
    return RetornosRV(df, cobertura, sorted(incluidos), excluidos, confianza_baja)


def matriz_correlacion(retornos: pd.DataFrame) -> pd.DataFrame:
    """Pairwise-complete, min_periods=MIN_SEMANAS_MINIMO (celda NaN si un
    par puntual no llega al piso, aunque cada ticker individualmente si)."""
    if retornos.shape[1] < 2:
        return pd.DataFrame(index=retornos.columns, columns=retornos.columns, dtype=float)
    return retornos.corr(min_periods=MIN_SEMANAS_MINIMO)


def pares_redundantes(corr: pd.DataFrame, umbral: float = UMBRAL_REDUNDANCIA) -> list[dict]:
    cols = list(corr.columns)
    pares = []
    for i, t1 in enumerate(cols):
        for t2 in cols[i + 1:]:
            v = corr.loc[t1, t2]
            if pd.notna(v) and v >= umbral:
                pares.append({"ticker_a": t1, "ticker_b": t2, "correlacion": round(float(v), 3)})
    pares.sort(key=lambda p: -p["correlacion"])
    return pares


def correlacion_ponderada(corr_fila: pd.Series, pesos: dict[str, float]) -> float | None:
    """Correlacion media de UN ticker (`corr_fila`, ej. corr.loc[candidato])
    contra el resto, ponderada por `pesos` (se ignoran los pares sin
    correlacion calculable -- no se aproxima con 0)."""
    num = den = 0.0
    for t, v in corr_fila.items():
        if pd.isna(v) or t not in pesos:
            continue
        num += pesos[t] * float(v)
        den += pesos[t]
    return round(num / den, 3) if den > 0 else None


def riesgo_cartera(retornos: pd.DataFrame, pesos: dict[str, float]) -> dict:
    """Volatilidad anualizada, contribucion al riesgo (Euler, % que suma
    100) y diversificacion efectiva, sobre las columnas de `retornos`
    (ya filtradas por `construir`). `pesos`: {ticker: peso 0-1} -- se
    RENORMALIZA dentro del subconjunto de `retornos.columns` (el llamador
    reporta aparte que % del valor total quedo cubierto, ver
    `cobertura_valor_pct`)."""
    cols = list(retornos.columns)
    if not cols:
        return {"vol_anual_pct": None, "contribucion_pct": {}, "diversificacion_efectiva": None}

    w = np.array([pesos.get(t, 0.0) for t in cols])
    if w.sum() <= 0:
        return {"vol_anual_pct": None, "contribucion_pct": {}, "diversificacion_efectiva": None}
    w = w / w.sum()

    cov = retornos.cov(min_periods=MIN_SEMANAS_MINIMO).reindex(index=cols, columns=cols).fillna(0.0)
    Sigma = cov.to_numpy()
    var_semanal = float(w @ Sigma @ w)
    if var_semanal <= 0:
        return {"vol_anual_pct": 0.0, "contribucion_pct": {t: 0.0 for t in cols}, "diversificacion_efectiva": None}

    vol_semanal = var_semanal ** 0.5
    vol_anual_pct = vol_semanal * (SEMANAS_POR_ANIO ** 0.5) * 100

    marginal = Sigma @ w                          # (Sigma w)_i
    contrib_vol = w * marginal / vol_semanal        # Euler: suma == vol_semanal
    contrib_pct = {t: round(float(c) / vol_semanal * 100, 2) for t, c in zip(cols, contrib_vol)}

    hhi = sum((c / 100) ** 2 for c in contrib_pct.values())
    diversificacion_efectiva = round(1 / hhi, 2) if hhi > 0 else None

    return {"vol_anual_pct": round(vol_anual_pct, 2), "contribucion_pct": contrib_pct,
            "diversificacion_efectiva": diversificacion_efectiva}
