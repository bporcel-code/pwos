"""
PWOS -- Watchlist de candidatas a comprar (Epica 19, Fase 3, v2.33).

`watchlist_rv.ticker` es el simbolo de Yahoo TAL CUAL ("BKNG", "YPFD.BA"): es lo
que se le pide a yfinance. Una candidata que ya es una posicion ABIERTA de la
cartera se rechaza (ya esta en el screener como cartera). Una posicion cerrada
si puede ser candidata (ej. volver a mirar KO).

Al borrar una candidata se borra tambien su fila de `fundamentales_rv` (seria una
fila huerfana), salvo que ese ticker sea una posicion abierta.

Excepciones: ValueError (dato invalido, 400) y LookupError (no existe, 404).
"""
import re
import sqlite3

from graham_rv import CUBETAS

TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,14}$")
CAMPOS_EDITABLES = ("nombre", "cubeta_sugerida", "notas")


def normalizar_ticker(ticker: str | None) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise ValueError(f"Ticker inválido: {ticker!r} (letras, números, punto o guión; ej. BKNG, YPFD.BA, BRK-B)")
    return t


def _validar_cubeta(cubeta) -> None:
    if cubeta is not None and cubeta not in CUBETAS:
        raise ValueError(f"cubeta_sugerida inválida: {cubeta!r} (válidas: {CUBETAS})")


def listar_watchlist(con) -> list[dict]:
    """`ratio_cedear` sale de `ratios_cedear` (v2.37, lista de BYMA) cruzando el
    ticker de Yahoo con `ticker_origen` (formato data912: BRK-B -> BRK.B); la
    columna vieja `watchlist_rv.ratio_cedear` (siempre NULL) queda solo como
    respaldo si la tabla no existe. Una candidata local (.BA) no tiene ratio."""
    base = """
        SELECT w.ticker, w.nombre, w.cubeta_sugerida, w.notas, w.agregada_en, {ratio},
               f.fecha_dato, f.tipo_instrumento, f.nombre
        FROM watchlist_rv w LEFT JOIN fundamentales_rv f ON f.ticker = w.ticker
        {join}
        ORDER BY w.agregada_en DESC, w.ticker;
    """
    try:
        filas = con.execute(base.format(
            ratio="COALESCE(r.ratio, w.ratio_cedear)",
            join="LEFT JOIN ratios_cedear r ON r.ticker_origen = REPLACE(w.ticker, '-', '.')")).fetchall()
    except sqlite3.OperationalError:
        filas = con.execute(base.format(ratio="w.ratio_cedear", join="")).fetchall()
    return [
        {"ticker": t, "nombre": n or nf, "cubeta_sugerida": c, "notas": no, "agregada_en": ag, "ratio_cedear": r,
         "fecha_dato": fd, "tiene_fundamentales": fd is not None, "tipo_instrumento": ti}
        for t, n, c, no, ag, r, fd, ti, nf in filas
    ]


def agregar(con, ticker: str, *, nombre: str | None = None, cubeta_sugerida: str | None = None,
            notas: str | None = None, tickers_cartera: set[str] | frozenset[str] = frozenset()) -> str:
    t = normalizar_ticker(ticker)
    _validar_cubeta(cubeta_sugerida)
    if t in tickers_cartera:
        raise ValueError(f"{t} ya es una posición abierta de tu cartera: ya aparece en el screener")
    if con.execute("SELECT 1 FROM watchlist_rv WHERE ticker = ?;", (t,)).fetchone():
        raise ValueError(f"{t} ya está en la watchlist")
    con.execute(
        "INSERT INTO watchlist_rv (ticker, nombre, cubeta_sugerida, notas) VALUES (?, ?, ?, ?);",
        (t, (nombre or "").strip() or None, cubeta_sugerida, (notas or "").strip() or None),
    )
    con.commit()
    return t


def actualizar(con, ticker: str, campos: dict) -> None:
    """UPDATE parcial: solo las claves presentes en `campos`."""
    t = normalizar_ticker(ticker)
    desconocidos = set(campos) - set(CAMPOS_EDITABLES)
    if desconocidos:
        raise ValueError(f"campos no editables: {sorted(desconocidos)}")
    if con.execute("SELECT 1 FROM watchlist_rv WHERE ticker = ?;", (t,)).fetchone() is None:
        raise LookupError(f"{t} no está en la watchlist")
    if "cubeta_sugerida" in campos:
        _validar_cubeta(campos["cubeta_sugerida"])
    limpios = {k: ((v.strip() or None) if isinstance(v, str) else v) for k, v in campos.items()}
    if limpios:
        sets = ", ".join(f"{c} = ?" for c in limpios)
        con.execute(f"UPDATE watchlist_rv SET {sets} WHERE ticker = ?;", (*limpios.values(), t))
        con.commit()


def eliminar(con, ticker: str, tickers_cartera: set[str] | frozenset[str] = frozenset()) -> None:
    t = normalizar_ticker(ticker)
    cur = con.execute("DELETE FROM watchlist_rv WHERE ticker = ?;", (t,))
    if cur.rowcount == 0:
        raise LookupError(f"{t} no está en la watchlist")
    if t not in tickers_cartera:
        con.execute("DELETE FROM fundamentales_rv WHERE ticker = ?;", (t,))
    con.commit()
