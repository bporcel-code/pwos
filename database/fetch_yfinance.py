"""
PWOS -- Capa de fetch de yfinance con retry acotado (indicadores de riesgo
y diversificacion).

Separado de clasificar_yfinance.py a proposito: ese modulo es reglas de
negocio puras (recibe un `info` ya resuelto, decide aceptar/rechazar/
manual), sin logica de red. Este es el unico modulo que habla con la red.

Retry acotado, no generico -- solo reintenta cuando el ticker RESOLVIO
pero falta un campo clave (longName/sector), la senal de respuesta
parcial/ruido intermitente de Yahoo encontrada en la verificacion del
2026-08-11 (PFE: 177 campos en el `info` pero longName ausente en una
corrida, completo en la siguiente). Un ticker sin cobertura real (404)
devuelve un `info` minimo (ej. {'trailingPegRatio': None}, largo 1) --
ahi no se reintenta, insistir no le agrega cobertura que no existe.
"""
import logging
import time

import yfinance as yf

logger = logging.getLogger(__name__)

LARGO_MINIMO_RESOLUCION = 10  # un 404 real devuelve 1 campo; una respuesta
                                # parcial pero real (PFE) trajo 177


def _info_parcial(info: dict | None) -> bool:
    """True = el ticker resolvio (info "grande") pero falta el nombre
    (ni longName ni shortName) o el sector -- vale la pena reintentar.
    False = info vacio/minimo (sin cobertura real, insistir no ayuda) o
    info ya suficiente para clasificar_yfinance.clasificar() -- en
    ambos casos no hay que reintentar.

    El nombre acepta longName O shortName (mismo fallback que
    clasificar_yfinance.clasificar(), encontrado con PFE: longName
    puede faltar de forma persistente mientras shortName si esta) --
    si no, este retry insistiria en vano por un campo que la capa de
    clasificacion ni siquiera exige de forma estricta."""
    if not info or len(info) < LARGO_MINIMO_RESOLUCION:
        return False
    tiene_nombre = bool(info.get("longName") or info.get("shortName"))
    tiene_sector = bool(info.get("sector"))
    return not (tiene_nombre and tiene_sector)


def fetch_yfinance_info(ticker: str, max_intentos: int = 3, espera_seg: float = 1.5) -> dict | None:
    """Pide el `info` de yfinance para `ticker` (ya con el sufijo de
    mercado resuelto, ver clasificar_yfinance.simbolo_yfinance).

    Reintenta hasta `max_intentos` veces SOLO mientras la respuesta sea
    parcial (ver _info_parcial). Si el ticker no resuelve en absoluto
    (404 / info minimo) devuelve en el primer intento, sin insistir. Si
    la consulta tira una excepcion (red caida, timeout), tampoco
    reintenta -- no es el caso de ruido documentado, mejor no enmascarar
    un error real."""
    info = None
    for intento in range(1, max_intentos + 1):
        try:
            info = yf.Ticker(ticker).get_info()
        except Exception as e:
            logger.warning("%s: excepcion en intento %d/%d -- %s", ticker, intento, max_intentos, e)
            return None

        if not _info_parcial(info):
            if intento > 1:
                logger.info("%s: se estabilizo en el intento %d/%d", ticker, intento, max_intentos)
            return info

        logger.warning(
            "%s: info parcial en intento %d/%d (falta nombre o sector), reintentando",
            ticker, intento, max_intentos,
        )
        if intento < max_intentos:
            time.sleep(espera_seg)

    logger.warning("%s: siguio parcial tras %d intentos, se devuelve tal cual", ticker, max_intentos)
    return info
