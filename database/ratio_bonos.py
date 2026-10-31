"""
PWOS -- Epica 14 Parte 2 (2026-09-14): "ratios" de
bonos como los usan los brokers (ej. AL30/GD30, ley local vs. ley NY) --
un indicador de cuanto vale un bono relativo a otro, dia a dia.

Primer diseno (descartado en la misma sesion): leer de
`mercado_renta_fija_historico` (snapshot diario que arranca
2026-09-11) -- 2 problemas reales, no hipoteticos:
  1. Solo 4 dias de historia (la tabla es nueva).
  2. `paridad_pct`/`precio_vn100` dan NULL para AL30/GD30 (y el resto de
     Bonares/Globales sin cronograma cacheado en cartera ni paridad
     publicada por Rava -- ver `_metricas_limpias` en
     cargar_mercado_renta_fija_rava.py) -- justo el caso que motivo el
     pedido.

El usuario aclaro el objetivo real: un indicador de "precio de cotizacion",
en tiempo real si se pudiera -- como no hay feed en vivo, con la ULTIMA
FOTO de precio que ya tenemos por bono, dia a dia. Eso YA EXISTE y es
mucho mas rico: `precios` (la misma tabla que usa TODO el dashboard
para valorizar cartera) tiene la cotizacion real de Rava, validada
contra data912, para cada ticker de renta fija que el usuario alguna vez
opero -- `backfill_precios_rava.py` trajo
hasta 2020 para varios (AL30: 1.457 dias, GD30: 1.458). Se usa esa
fuente aca en vez de duplicar/backfillear nada nuevo.

Limite real (documentado, no escondido): `precios` solo cubre los
tickers que aparecen en `activos`. Hasta 2026-09-18 eso era SOLO lo que
el usuario alguna vez opero (~46 tickers) -- pedido del usuario ese dia: sumar
el universo COMPLETO de Bonares/Globales/CER (69 especies de Rava,
incluidas las 3 variantes O/C/D de cada bono), no solo lo que tiene en
cartera, porque son justo las 3 categorias que mas se usan para ratios
de precio. `alta_activos_referencia.py` (catalogo) +
`backfill_precios_rava_referencia.py` (precio
historico via Rava) resuelven esas 3 categorias -- 100% de cobertura
ahi. El resto de renta fija (ONs, BOPREAL, Provinciales, LECAP/BONCAP)
sigue acotado a lo operado -- extenderlo a los ~890 de todo el universo
de Rava seguiria siendo otro proyecto aparte (mismo volumen ya
descartado para Carry en tir_bonos.py).

Sin restriccion de categoria en el par (a diferencia de
breakeven_implicito.py, que empareja automatico por duration DENTRO de
una categoria) -- aca el par lo elige el usuario a mano.
"""

import clasificar_bonos
from valorizacion import RENTA_FIJA


def tickers_con_historial(con) -> list[dict]:
    """Universo de tickers de renta fija con al menos 1 fila en
    `precios` -- lo que puede ofrecerse en los selectores del
    comparador (no los ~890 de Rava, ver docstring del modulo).
    `moneda` es la de la fila MAS RECIENTE de cada ticker (ya
    clasificada por `MONEDA_PRECIO_RAVA`/`MONEDA_PRECIO_DATA912` al
    cargarse -- no se re-decide aca)."""
    placeholders = ",".join("?" * len(RENTA_FIJA))
    filas = con.execute(
        f"""
        SELECT a.ticker, p.fecha, p.moneda
        FROM precios p
        JOIN activos a ON a.id = p.activo_id
        WHERE a.tipo IN ({placeholders})
        ORDER BY a.ticker, p.fecha;
        """,
        tuple(RENTA_FIJA),
    ).fetchall()

    por_ticker: dict[str, dict] = {}
    for ticker, fecha, moneda in filas:
        info = por_ticker.setdefault(ticker, {"dias": 0, "desde": fecha})
        info["dias"] += 1
        info["moneda"] = moneda  # se recorre en orden de fecha ASC -> la ultima fila deja la moneda vigente
        info["hasta"] = fecha

    resultado = []
    for ticker, info in sorted(por_ticker.items()):
        categoria = clasificar_bonos.clasificar(ticker)
        resultado.append({
            "ticker": ticker,
            "categoria": categoria,
            "categoria_label": clasificar_bonos.CATEGORIA_LABEL[categoria],
            "moneda": info["moneda"],
            "dias": info["dias"],
            "desde": info["desde"],
            "hasta": info["hasta"],
        })
    return resultado


def _serie_precios(con, ticker: str, desde: str | None) -> dict[str, float]:
    condiciones = ["a.ticker = ?"]
    params: list = [ticker.strip().upper()]
    if desde:
        condiciones.append("p.fecha >= ?")
        params.append(desde)
    filas = con.execute(
        f"""
        SELECT p.fecha, p.precio
        FROM precios p
        JOIN activos a ON a.id = p.activo_id
        WHERE {' AND '.join(condiciones)}
        ORDER BY p.fecha;
        """,
        params,
    ).fetchall()
    return {fecha: precio for fecha, precio in filas}


def ratio_historico(con, ticker_a: str, ticker_b: str, desde: str | None = None) -> list[dict]:
    """Serie diaria de precio_a/precio_b, fuente `precios` (cotizacion
    real de Rava ya validada -- ver docstring del modulo). Solo los
    dias donde AMBOS tickers tienen precio ese dia exacto (INTERSECCION,
    no union ni interpolacion) -- si falta uno de los dos, se saltea el
    punto en vez de inventar algo. `desde` (YYYY-MM-DD) opcional, para
    no traer anios de historia cuando el caller solo quiere una ventana
    reciente."""
    serie_a = _serie_precios(con, ticker_a, desde)
    serie_b = _serie_precios(con, ticker_b, desde)

    resultado = []
    for fecha in sorted(set(serie_a) & set(serie_b)):
        precio_a, precio_b = serie_a[fecha], serie_b[fecha]
        resultado.append({
            "fecha": fecha,
            "precio_a": precio_a,
            "precio_b": precio_b,
            "ratio": round(precio_a / precio_b, 4) if precio_b else None,
        })
    return resultado
