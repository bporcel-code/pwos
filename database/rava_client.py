"""
PWOS -- Cliente de lectura de la API de Rava (mercado.rava.com), para
cronograma de pagos de bonos + legislacion + tipo de instrumento
(indicadores de riesgo y diversificacion, renta fija).

Confirmado antes de escribir el parser (2026-08-11), no asumido:
`www.rava.com/perfil/{ticker}` es HTML server-rendido pero NO incluye
este dato -- esta embebido via <iframe> desde `mercado.rava.com`, que es
una SPA de React (bundle JS, no HTML server-rendido). En vez de
Playwright, se encontro dentro del bundle JS de la SPA el fetch() real
que arma la tabla en pantalla:

  - GET https://mercado.rava.com/api/prices/bonos-flujo/{ticker}?incluir=todo
    -> cronograma completo (pasado + futuro) de UN bono: lista de
    {fecha_pago, cupon (interes por VN 100), principal (amortizacion
    por VN 100), saldo, total, tipo ('pasado'/'futuro')}. Sin
    autenticacion, JSON directo, sin necesidad de headers especiales
    mas alla de un User-Agent de navegador.

  - GET https://mercado.rava.com/api/prices/bonos -> listado de TODOS
    los bonos del mercado (1143 al momento de probarlo) en un solo
    llamado -- cada item trae {especie, nombre, tipo, ley, tir,
    duration, paridad, vencimiento, ...}. `tipo` es la clasificacion de
    instrumento de Rava (ej. 'T�tulo P�blico'), `ley` es la legislacion
    ('ARG'/'NY'). Se pide UNA vez y se arma un diccionario en memoria --
    no tiene sentido pedir bono por bono cuando ya viene todo junto.

Cobertura confirmada contra una cartera de renta fija real (2026-08-11):
todos los tickers en ambos endpoints, incluidos BOPREAL
y ONs -- no hubo ningun caso que necesitara Playwright. Puede
que un ticker nuevo en el futuro no tenga cobertura -- ver
dry_run_bonos_rava.py, que no rompe el batch si un ticker falla.
"""
import requests

BASE_URL = "https://mercado.rava.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT_SEG = 20


def obtener_flujo_bono(ticker: str) -> dict | None:
    """Cronograma completo de un bono (pasado + futuro). Devuelve None
    si el ticker no tiene cobertura en Rava (404) o la respuesta no
    trae flujos -- nunca levanta excepcion por un ticker sin cobertura,
    para no romper un batch de varios tickers (ver dry_run_bonos_rava.py)."""
    try:
        resp = requests.get(
            f"{BASE_URL}/api/prices/bonos-flujo/{ticker}",
            params={"incluir": "todo"},
            headers=HEADERS,
            timeout=TIMEOUT_SEG,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    datos = resp.json()
    if not datos.get("flujos"):
        return None
    return datos


def obtener_referencia_bonos() -> dict:
    """Listado completo de bonos de Rava, UN solo llamado, indexado por
    ticker ('especie') para lookup O(1). Lanza excepcion si el llamado
    en si falla (a diferencia de obtener_flujo_bono, esto no es
    por-ticker -- si esto falla, nada tiene sentido seguir)."""
    resp = requests.get(
        f"{BASE_URL}/api/prices/bonos",
        headers=HEADERS,
        timeout=TIMEOUT_SEG,
    )
    resp.raise_for_status()
    datos = resp.json().get("datos", [])
    return {d["especie"]: d for d in datos}
