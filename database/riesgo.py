"""
PWOS -- Motor de scorecard de riesgo de cartera.

Metodologia propia (definida por el usuario y Claude, NO viene de ninguna
planilla ni proveedor externo) -- sistematiza el criterio que el usuario ya
aplica "a ojo" hoy. Documentada completa (pesos, cortes, fuente de cada
factor) en ESTADO_PWOS.md, seccion "Metodologia de riesgo de cartera" --
este modulo es la implementacion, esa seccion es la referencia para
ajustarla con criterio.

Reglas de negocio puras: no toca la base ni la red (mismo patron de
separacion que clasificar_yfinance.py/fetch_yfinance.py). El caller
(cargar_factores_riesgo.py / api.py) resuelve primero los datos crudos
(vencimiento desde cronograma_pagos_bonos, beta/deuda_patrimonio desde
yfinance) y se los pasa ya resueltos a las funciones de aca.

Efectivo NO entra en este scorecard (sin riesgo de mercado en este
sentido) -- mismo criterio que ya se aplica para "Ganancia/perdida".

--------------------------------------------------------------------
BONOS SOBERANOS / PROVINCIALES -- 4 factores 1-5, ponderados:
  - Rating crediticio (35%): AAA/AA=5, A/BBB=4, BB=3, B=2, CCC o sin
    rating=1. Escala INTERNACIONAL (S&P/Fitch/Moody's global), no la
    escala local (FIX SCR/Moody's Local) -- no son comparables 1:1.
  - Duration / anios a vencimiento como proxy (25%): <2=5, 2-5=4,
    5-10=3, 10-20=2, >20=1.
  - Legislacion (25%): ley extranjera (NY)=5, ley local=2.
  - Tipo de instrumento (15%): soberano con garantias=5, soberano
    estandar=3, provincial=3.
  Score final = suma ponderada. Bucket: >=4.0 Conservador, 2.5-4.0
  Moderado, <2.5 Arriesgado.

ONs -- mismo esquema y mismos pesos, pero el factor de credito (35%) se
resuelve con el ratio Deuda/Patrimonio de la empresa cuando no hay
rating formal (que es el caso de las 8 ONs en cartera hoy): <1=5,
1-2=3, >2=1. Duration/legislacion/tipo de instrumento se calculan igual
que para soberanos/provinciales -- "tipo de instrumento" no tiene una
categoria propia para ON en la metodologia original, asi que se trata
como "estandar" (score 3) salvo que se confirme una garantia real
(ninguna de las ONs actuales la tiene) -- interpretacion de Code,
marcada explicita aca para que el usuario la corrija si no es lo que queria.

ACCIONES / CEDEARs -- 1 solo factor, Beta (yfinance): <0.8=Conservador,
0.8-1.3=Moderado, >1.3=Arriesgado. Para poder combinar bonos y equity
en un mismo score numerico 1-5 (necesario para el metric card de
"Riesgo de cartera" ponderado por posicion), cada bucket de beta se
mapea a un score representativo (5/3/1) -- incluido a proposito en la
metodologia original solo como bucket, esta correspondencia numerica es
una extension de Code para la agregacion de cartera, no un cuarto
factor nuevo.
--------------------------------------------------------------------
"""
from datetime import date

import configuracion

# ============================================================
# Pesos y cortes -- ajustables aca, no hardcodeados en el medio
# del calculo.
# ============================================================

PESO_CREDITO = 0.35  # rating (soberano/provincial) o Deuda/Patrimonio (ON)
PESO_DURATION = 0.25
PESO_LEGISLACION = 0.25
PESO_TIPO_INSTRUMENTO = 0.15

CORTE_CONSERVADOR = 4.0  # score >= esto -> "Conservador"
CORTE_MODERADO = 2.5     # score >= esto (y < CORTE_CONSERVADOR) -> "Moderado"
                          # score < CORTE_MODERADO -> "Arriesgado"

SIN_DATO = "No verificable"  # mismo criterio que un ETF sin beta: no se inventa un valor


# ============================================================
# Rating soberano/provincial -- escala internacional (S&P/Fitch/
# Moody's alineadas en B- tras la ola de upgrades de 2026).
# Investigado y confirmado 2026-08-22 (ver ESTADO_PWOS.md para las
# fuentes). Ajustar aca cuando cambien los ratings publicados -- no
# hay forma de automatizar esta consulta hoy, es manual.
# ============================================================

RATING_EMISOR_SOBERANO = {
    "NACION": "B-",
    "NEUQUEN": "B-",
    "SALTA": "B-",  # S&P (escala global) -- FIX SCR (escala local, BBB-) no se usa aca
}

# tipo_activo in (bono_soberano, bono_cer, lecap) -> emisor NACION.
# bono_provincial -> emisor especifico por ticker.
TICKER_EMISOR_SOBERANO = configuracion.get("bonos", "emisor_soberano", default={})

# Tipo de instrumento -- clasificacion "con garantias" vs "estandar" vs
# "provincial", confirmada por el usuario 2026-08-22. Un BOPREAL,
# emitido por el BCRA con garantia real (derechos futuros de compra de
# divisas) -- genuinamente distinto de un Bonar/Global estandar.
TICKER_TIPO_INSTRUMENTO_SOBERANO = configuracion.get("bonos", "tipo_instrumento_soberano", default={})

SCORE_TIPO_INSTRUMENTO = {
    "soberano_garantia": 5,
    "soberano_estandar": 3,
    "provincial": 3,
    "on_estandar": 3,  # ver docstring del modulo -- interpretacion de Code para ON
}

# Emisor de cada ON -> ticker de yfinance para su Deuda/Patrimonio
# (empresa que cotiza, no la ON en si). None = emisor privado, sin
# ticker en ningun lado -- queda "No verificable" (decision del usuario
# 2026-08-22, mismo criterio que un ETF sin beta).
ON_EMISOR_YFINANCE = configuracion.get("bonos", "on_emisor_yfinance", default={})


# Piso de riesgo para acciones argentinas listadas localmente
# (tipo_activo='accion' -- a diferencia de 'cedear', que es el
# subyacente extranjero, ver valorizacion.RENTA_VARIABLE). Decision del
# usuario 2026-08-22: verificado que el beta de yfinance de las acciones
# .BA medidas daba todas "Conservador" (betas entre -0.88 y 0.69) -- se mide
# contra el Merval en pesos, no contra un benchmark en dolares, y no
# captura el riesgo cambiario/de convertibilidad, que nunca es cero
# para algo denominado en pesos. Los CEDEARs (empresas extranjeras) NO
# llevan este piso -- ya daban resultados sensatos con el beta tal cual
# (ej. MELI 'Arriesgado' con beta 1.31).
#
# El piso actua SOLO sobre el bucket final, nunca sobre el beta ni el
# score numerico (se guardan tal cual vienen de yfinance, para que el
# dato crudo siga siendo transparente/auditable) -- si el calculo
# normal da 'Conservador' para una accion argentina, el bucket queda
# forzado a BUCKET_PISO_MINIMO en su lugar.
PISO_RIESGO_TIPO_ACTIVO = {"accion"}
BUCKET_PISO_MINIMO = "Moderado"


# ============================================================
# Funciones de score por factor (cada una 1-5, o None si no hay dato)
# ============================================================

def _categoria_base_rating(rating: str) -> str:
    """'B-' -> 'B', 'BBB+' -> 'BBB' (los cortes de la metodologia son
    por letra base, +/- es matiz dentro de la misma categoria)."""
    return rating.upper().rstrip("+-")


def score_rating(rating: str | None) -> int:
    if not rating:
        return 1  # "sin rating" -- mismo bucket que CCC, por definicion de la metodologia
    base = _categoria_base_rating(rating)
    if base in ("AAA", "AA"):
        return 5
    if base in ("A", "BBB"):
        return 4
    if base == "BB":
        return 3
    if base == "B":
        return 2
    return 1  # CCC, CC, C, D o no reconocido


def score_deuda_patrimonio(ratio: float | None) -> int | None:
    if ratio is None:
        return None
    if ratio < 1:
        return 5
    if ratio <= 2:
        return 3
    return 1


def anios_a_vencimiento(vencimiento: date, hoy: date | None = None) -> float:
    hoy = hoy or configuracion.hoy()
    return (vencimiento - hoy).days / 365.25


def score_duration(anios: float) -> int:
    if anios < 2:
        return 5
    if anios <= 5:
        return 4
    if anios <= 10:
        return 3
    if anios <= 20:
        return 2
    return 1


def score_legislacion(legislacion: str) -> int:
    """`legislacion` es el campo tal cual viene de bonos_referencia
    ('ARG'/'NY', ver rava_client.py)."""
    return 5 if legislacion == "NY" else 2


def score_tipo_instrumento(categoria: str) -> int:
    return SCORE_TIPO_INSTRUMENTO[categoria]


def score_beta(beta: float | None) -> int | None:
    if beta is None:
        return None
    if beta < 0.8:
        return 5
    if beta <= 1.3:
        return 3
    return 1


def bucket_de_score(score: float) -> str:
    if score >= CORTE_CONSERVADOR:
        return "Conservador"
    if score >= CORTE_MODERADO:
        return "Moderado"
    return "Arriesgado"


# ============================================================
# Orquestacion por instrumento -- combina los factores ya resueltos
# (el caller los saca de la base/yfinance) en un score final + bucket.
# ============================================================

def calcular_score_renta_fija(
    *,
    rating: str | None,
    deuda_patrimonio: float | None,
    anios_vencimiento: float,
    legislacion: str,
    tipo_instrumento_categoria: str,
) -> dict:
    """Bono soberano/provincial (pasar `rating`, `deuda_patrimonio=None`)
    u ON (pasar `rating=None`, `deuda_patrimonio=<ratio o None>`).

    Si el factor de credito no se puede resolver (ON de emisor privado
    sin rating ni Deuda/Patrimonio), el instrumento entero queda
    "No verificable" -- no se recalculan pesos sobre los otros 3
    factores para no rediseñar la metodologia en silencio (35% del
    score quedaria sin ninguna base real)."""
    if rating is not None:
        score_credito = score_rating(rating)
        fuente_credito = "rating"
    else:
        score_credito = score_deuda_patrimonio(deuda_patrimonio)
        fuente_credito = "deuda_patrimonio"

    if score_credito is None:
        return {
            "score_final": None,
            "bucket": SIN_DATO,
            "detalle": {"credito": {"fuente": fuente_credito, "valor": deuda_patrimonio, "score": None}},
        }

    score_dur = score_duration(anios_vencimiento)
    score_leg = score_legislacion(legislacion)
    score_tipo = score_tipo_instrumento(tipo_instrumento_categoria)

    score_final = (
        score_credito * PESO_CREDITO
        + score_dur * PESO_DURATION
        + score_leg * PESO_LEGISLACION
        + score_tipo * PESO_TIPO_INSTRUMENTO
    )

    return {
        "score_final": round(score_final, 2),
        "bucket": bucket_de_score(score_final),
        "detalle": {
            "credito": {"fuente": fuente_credito, "valor": rating if rating is not None else deuda_patrimonio, "score": score_credito},
            "duration": {"anios": round(anios_vencimiento, 2), "score": score_dur},
            "legislacion": {"valor": legislacion, "score": score_leg},
            "tipo_instrumento": {"categoria": tipo_instrumento_categoria, "score": score_tipo},
        },
    }


def calcular_score_equity(beta: float | None, tipo_activo: str) -> dict:
    """`tipo_activo` es 'accion' (BYMA local) o 'cedear' (subyacente
    extranjero) -- determina si aplica el piso de riesgo (ver
    PISO_RIESGO_TIPO_ACTIVO)."""
    score = score_beta(beta)
    if score is None:
        return {"score_final": None, "bucket": SIN_DATO, "detalle": {"beta": {"valor": None, "score": None}}}

    bucket = bucket_de_score(score)
    piso_aplicado = tipo_activo in PISO_RIESGO_TIPO_ACTIVO and bucket == "Conservador"
    if piso_aplicado:
        bucket = BUCKET_PISO_MINIMO

    return {
        "score_final": float(score),
        "bucket": bucket,
        "detalle": {"beta": {"valor": beta, "score": score}, "piso_riesgo_aplicado": piso_aplicado},
    }
