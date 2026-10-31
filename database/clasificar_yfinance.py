"""
PWOS -- Reglas de aceptacion de datos de yfinance para las tablas nuevas
`clasificacion_cedears` / `datos_yfinance` (indicadores de riesgo y
diversificacion).

Bug encontrado 2026-08-11 (probar_yfinance.py, primera corrida): pedir
el ticker pelado de una accion local puede resolver contra otro mercado
por coincidencia de codigo -- 'AGRO' pelado matcheo con Adecoagro S.A.
(NYSE, Luxemburgo) en vez de Agrometal S.A. (BYMA, la empresa real que
tiene el usuario). Los datos que trae son reales, pero de la empresa
equivocada -- un falso positivo silencioso, pasa cualquier chequeo de
"info completo". Con el sufijo de mercado (.BA) yfinance matcheo la
empresa correcta en los 8 tickers locales probados (incluido AGRO.BA =
Agrometal). El cross-check de nombre (NOMBRE_ESPERADO) queda como
segunda barrera para los tickers marcados como sensibles, no como unico
mecanismo de defensa -- no hay forma de detectar una colision de este
tipo solo con el sufijo.
"""
import unicodedata


def simbolo_yfinance(ticker: str, tipo_activo: str) -> str:
    """Simbolo real a pedirle a yfinance para un activo de la cartera.
    Acciones locales necesitan el sufijo de BYMA para no colisionar con
    un ticker homonimo en otro mercado (ver docstring del modulo);
    CEDEARs ya usan el ticker canonico del subyacente en EE.UU. desde
    los importadores de brokers, sin sufijo."""
    if tipo_activo == "accion":
        return f"{ticker}.BA"
    return ticker


# Tickers "sensibles": donde ya se confirmo (AGRO) o existe riesgo real
# (LOMA, que tiene una ADR real en NYSE bajo el mismo ticker que su
# listado local en BYMA -- mismo ticker, dos empresas... misma empresa
# en este caso, pero dos listados distintos) de que yfinance resuelva
# contra un listado equivocado. Ampliar a mano si aparece un caso nuevo
# -- no se puede detectar la colision automaticamente, solo confirmarla
# contra un nombre esperado.
NOMBRE_ESPERADO = {
    "AGRO": "Agrometal",
    "LOMA": "Loma Negra",
}


def _normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_acentos.lower()


def validar_nombre(ticker: str, long_name: str | None) -> bool:
    """True si el ticker no es sensible (no necesita cross-check), o si
    el long_name devuelto por yfinance contiene el nombre esperado."""
    esperado = NOMBRE_ESPERADO.get(ticker)
    if esperado is None:
        return True
    if not long_name:
        return False
    return _normalizar(esperado) in _normalizar(long_name)


def clasificar(ticker: str, info: dict | None) -> dict:
    """Aplica la regla de aceptacion final sobre el `info` ya obtenido
    de yfinance.Ticker(simbolo).get_info() para un ticker de la cartera
    vigente. `ticker` es el ticker de PWOS (sin sufijo de mercado) --
    se usa para el cross-check de NOMBRE_ESPERADO, no para pedir datos.

    Devuelve un dict con 'estado' en:
      - 'aceptado'          -> EQUITY con nombre/sector, paso el cross-check si aplicaba
      - 'etf'                -> quoteType == 'ETF', tratamiento propio (ver punto 4 del prompt)
      - 'rechazado_nombre'   -> EQUITY pero el cross-check de nombre fallo (colision de ticker)
      - 'manual'              -> sin datos, quoteType no reconocido, o campos clave ausentes

    Nombre: se usa `longName`, y si viene ausente se cae a `shortName`
    (encontrado 2026-08-11 con PFE -- `longName` puede faltar de forma
    persistente, no solo por ruido intermitente, mientras `shortName` y
    `sector` siguen presentes; no tiene sentido mandar a manual un
    ticker con cobertura real solo porque falta ese campo puntual).
    """
    quote_type = info.get("quoteType") if info else None
    long_name = (info.get("longName") or info.get("shortName")) if info else None

    if not info or quote_type is None:
        return {"ticker": ticker, "estado": "manual", "motivo": "sin datos (404 / info vacio)"}

    if quote_type == "ETF":
        return {
            "ticker": ticker,
            "estado": "etf",
            "long_name": long_name,
            "category": info.get("category"),
            "fund_family": info.get("fundFamily"),
            "total_assets": info.get("totalAssets"),
        }

    if quote_type != "EQUITY":
        return {
            "ticker": ticker,
            "estado": "manual",
            "motivo": f"quoteType={quote_type!r} no es EQUITY ni ETF",
        }

    if not long_name or not info.get("sector"):
        return {"ticker": ticker, "estado": "manual", "motivo": "longName/shortName o sector ausente"}

    if not validar_nombre(ticker, long_name):
        return {
            "ticker": ticker,
            "estado": "rechazado_nombre",
            "long_name": long_name,
            "motivo": f"longName {long_name!r} no matchea nombre esperado {NOMBRE_ESPERADO.get(ticker)!r}",
        }

    return {
        "ticker": ticker,
        "estado": "aceptado",
        "long_name": long_name,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "beta": info.get("beta"),
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE"),
        "dividend_yield": info.get("dividendYield"),
        "roe": info.get("returnOnEquity"),
        "revenue_growth": info.get("revenueGrowth"),
    }
