"""
PWOS -- Correlacion y contribucion al riesgo de Renta Variable (Epica 19,
Fase 4, 2026-09-22). Reemplaza el calculo diario en USD MEP de la Epica 11
original (Herramienta 3) -- ver retornos_rv.py para la base de retornos
(subyacente USD para CEDEARs, MEP para acciones locales, excepcion
documentada a ADR-001) y las formulas de riesgo (Euler / diversificacion
efectiva). Solo informativo -- el motor de decision sigue siendo
rebalanceo_rv.py, esto nunca dice comprar/vender.
"""
import pandas as pd

from rebalanceo_rv import TIPOS_RENTA_VARIABLE
from retornos_rv import (
    MIN_MESES_ALTA_CONFIANZA,
    MIN_SEMANAS_MINIMO,
    UMBRAL_REDUNDANCIA,
    construir,
    matriz_correlacion,
    pares_redundantes,
    riesgo_cartera,
)

# "principales posiciones" (3.7) para el mapa de calor -- un heatmap de las
# 23 posiciones vigentes seria ilegible en un panel chico; el resto de los
# numeros (pares redundantes, contribucion al riesgo, diversificacion) SI
# cubren todo el universo incluido, sin recortar.
TOP_N_MAPA_CALOR = 8


def _posiciones_vigentes(con) -> list[tuple[str, str]]:
    return con.execute(
        f"""
        SELECT DISTINCT a.ticker, a.tipo FROM vista_posiciones vp JOIN activos a ON a.id = vp.activo_id
        WHERE a.tipo IN ({",".join("?" * len(TIPOS_RENTA_VARIABLE))});
        """,
        TIPOS_RENTA_VARIABLE,
    ).fetchall()


def analisis_riesgo_rv(con, pesos: dict[str, float]) -> dict:
    """`pesos`: {ticker: pct_rv/100} de TODAS las posiciones vigentes -- ya
    calculado por tenencias_rv.py, este modulo no vuelve a valorizar nada."""
    posiciones = _posiciones_vigentes(con)
    r = construir(con, posiciones)
    corr = matriz_correlacion(r.retornos)
    pares = pares_redundantes(corr, UMBRAL_REDUNDANCIA)
    riesgo = riesgo_cartera(r.retornos, pesos)

    top = sorted(r.incluidos, key=lambda t: -pesos.get(t, 0.0))[:TOP_N_MAPA_CALOR]
    mapa_calor = None
    if top:
        sub = corr.loc[top, top]
        mapa_calor = {
            "tickers": top,
            "valores": [[(round(float(v), 3) if pd.notna(v) else None) for v in fila] for fila in sub.to_numpy()],
        }

    cobertura_valor_pct = round(sum(pesos.get(t, 0.0) for t in r.incluidos) * 100, 1)

    return {
        "base": "Retornos semanales: subyacente USD para CEDEARs, MEP para acciones locales (excepción documentada a ADR-001).",
        "pares_redundantes": pares,
        "umbral_redundancia": UMBRAL_REDUNDANCIA,
        "sin_datos_suficientes": r.excluidos,
        "confianza_baja": r.confianza_baja,
        "min_semanas_minimo": MIN_SEMANAS_MINIMO,
        "min_meses_alta_confianza": MIN_MESES_ALTA_CONFIANZA,
        "cobertura": r.cobertura,
        "mapa_calor": mapa_calor,
        "volatilidad_anual_pct": riesgo["vol_anual_pct"],
        "contribucion_riesgo_pct": riesgo["contribucion_pct"],
        "diversificacion_efectiva": riesgo["diversificacion_efectiva"],
        "cobertura_valor_pct": cobertura_valor_pct,
    }
