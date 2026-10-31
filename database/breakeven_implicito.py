"""
PWOS -- breakeven de inflacion y devaluacion implicita (Epica 11,
2026-09-11).

Metodologia:
  - Breakeven de inflacion: cada bono CER se empareja con el bono a
    tasa fija (LECAP/BONCAP) de duration MAS CERCANA en todo el
    universo de Rava (no solo cartera -- mas candidatos, match mas
    fino). Breakeven = TIR_tasa_fija - TIR_CER (RESTA simple). Ambas
    TIR estan en la MISMA moneda (ARS) -- la resta nominal-real es
    directamente el spread de tasas, misma convencion que el breakeven
    de inflacion de TIPS en EEUU (Treasury nominal - TIPS real), no
    hace falta una formula multiplicativa porque no hay cruce de
    moneda de por medio.
  - Devaluacion implicita: cada bono a tasa fija en pesos (LECAP/
    BONCAP) se empareja con el bono dollar-linked o hard-dollar
    (Bonares/Globales/dollar-linked, la union de las 3 categorias) de
    duration mas cercana. Devaluacion = (1+TIR_ARS)/(1+TIR_USD) - 1
    (PARIDAD de tasas, no resta simple) -- a diferencia del breakeven
    de inflacion, aca se cruzan 2 MONEDAS distintas: la relacion entre
    una tasa en pesos y una tasa en dolares que el mercado arbitra es
    multiplicativa por definicion (paridad de tasas de interes,
    cubierta/no cubierta), restarlas directo subestima la devaluacion
    a las magnitudes de tasas que maneja Argentina (ej. TIR ARS 30% vs
    TIR USD 8%: resta simple da 22 pp, la paridad da ~20,4% -- la
    diferencia no es un redondeo, es lo que se arbitra realmente).

Matching por duration MAS CERCANA dentro de cada categoria (no
interpolacion de curva sobre toda la categoria) -- decision del usuario,
2026-09-11: mas trazable ("con que bono exacto se comparo") y con
~10-30 especies liquidas por categoria interpolar no aporta mucha
precision extra.

Filtros de calidad de dato (mismo universo, mismos hallazgos que ya
documento `tir_bonos.candidatos_reemplazo` -- pero NO se reusa esa
funcion tal cual, ver nota):
  - `duration >= DURATION_MINIMA_CANDIDATO`: un bono a semanas del
    vencimiento anualiza cualquier ruido de precio en una TIR
    disparatada -- ver tir_bonos.py para el ejemplo real encontrado
    (SNSBD/SNSBO, TIR ~966%/767%).
  - `TICKERS_TIR_CORRUPTA`: exclusion explicita de TLCJO/YMCPO/CH24D
    (precio ~1.5x mas grande que paridad*valor_tecnico -> TIR de Rava
    corrupta, 100-386%, bug de feed ya encontrado 2026-09-07).

NOTA (encontrado armando este modulo, 2026-09-11): el filtro de
consistencia "precio vs. paridad*valor_tecnico" que usa
`candidatos_reemplazo` da por sentado que `paridad`/`valor_tecnico` son
NOT NULL en las filas que evalua -- ahi funciona porque ese filtro ya
se aplica DESPUES de un `WHERE paridad IS NOT NULL AND valor_tecnico IS
NOT NULL` (sqlite, en la query de esa funcion). Reusado tal cual aca
rompia: `paridad` viene en `0.0` (no NULL) para la enorme mayoria del
universo -- documentado en cargar_mercado_renta_fija_rava.py como
comportamiento NORMAL de Rava, no corrupcion -- y con paridad=0 la
condicion `ABS(precio - paridad*valor_tecnico) <= tol*ABS(paridad*
valor_tecnico)` se vuelve `ABS(precio) <= 0`, falsa para cualquier
precio no nulo. Aplicado sobre las categorias que necesita este modulo
(CER/tasa fija/dolar) el resultado era CERO filas -- ninguna tiene
paridad != 0 en el bulk de Rava. Por eso aca se excluyen los 3 tickers
conocidos por nombre en vez de repetir ese filtro generico.

No se filtran especies "variante" (sufijo C/D de la misma emision) del
universo de matching -- son economicamente fungibles (mismos flujos),
su TIR deberia ser casi identica a la de la especie base, incluirlas
no distorsiona el resultado (en el peor caso, el match elige la
variante en vez de la base, sin cambiar la lectura).

Epica 14 Parte 1 (2026-09-14): cada fila suma un veredicto `conviene`,
contra la expectativa de mercado (REM del BCRA, ya cacheado en
`benchmarks_macro`) -- sin esa referencia el breakeven/devaluacion es
un punto de indiferencia, no dice nada por si solo. El valor de REM
usado se pasa desde `api.py` (mismo patron que `mep`/`ccl` en
`sensibilidad_tasa_cartera`) para no acoplar este modulo a la tabla
`benchmarks_macro`. Ni el breakeven/devaluacion de cada fila ni el REM
se prorratean por duration del par -- ambos ya vienen anualizados
(mismo estilo que las TIR), se comparan directo.
"""

import clasificar_bonos
from tir_bonos import DURATION_MINIMA_CANDIDATO

TICKERS_TIR_CORRUPTA = {"TLCJO", "YMCPO", "CH24D"}

CATEGORIAS_TASA_FIJA = ("letra", "boncap")
CATEGORIAS_USD = ("dollar_linked", "bonar", "global")

UMBRAL_EMPATE_PP = 0.05  # diferencia menor a esto = empate tecnico, sin veredicto


def _conviene(esperado: float | None, implicito: float | None, si_mayor: str, si_menor: str) -> str | None:
    """Veredicto de que lado conviene: `esperado` (REM) contra
    `implicito` (breakeven/devaluacion de la fila). None si falta
    cualquiera de los dos valores, o si la diferencia es un empate
    tecnico (< UMBRAL_EMPATE_PP)."""
    if esperado is None or implicito is None:
        return None
    diferencia = esperado - implicito
    if abs(diferencia) < UMBRAL_EMPATE_PP:
        return None
    return si_mayor if diferencia > 0 else si_menor


def _universo_confiable(con) -> list[dict]:
    """ticker/categoria/tir_pct/duration de mercado_renta_fija_rava,
    solo especies con TIR/duration utilizables (ver docstring del
    modulo para el motivo de cada filtro)."""
    filas = con.execute(
        """
        SELECT ticker, tipo_instrumento, legislacion, tir, duration
        FROM mercado_renta_fija_rava
        WHERE tir IS NOT NULL AND duration IS NOT NULL
          AND duration >= ?;
        """,
        (DURATION_MINIMA_CANDIDATO,),
    ).fetchall()
    universo = []
    for ticker, tipo_instrumento, legislacion, tir, duration in filas:
        if ticker in TICKERS_TIR_CORRUPTA:
            continue
        categoria = clasificar_bonos.clasificar(ticker, tipo_instrumento, legislacion)
        universo.append({
            "ticker": ticker, "categoria": categoria,
            "tir_pct": tir * 100, "duration": duration,
        })
    return universo


def _mas_cercano_por_duration(candidatos: list[dict], duration_objetivo: float) -> dict | None:
    """El candidato con duration mas cercana a `duration_objetivo`, o
    None si `candidatos` esta vacia."""
    if not candidatos:
        return None
    return min(candidatos, key=lambda c: abs(c["duration"] - duration_objetivo))


def breakeven_inflacion(con, inflacion_esperada_rem_pct: float | None = None) -> list[dict]:
    """1 fila por bono CER, emparejado con el bono a tasa fija (LECAP/
    BONCAP) de duration mas cercana -- `breakeven_pct` = cuanta
    inflacion esta pricing in el mercado (resta de TIR, misma moneda).
    Ordenado por duration del bono CER (lectura tipo "curva", corto a
    largo plazo). Sin fila para un CER sin ningun candidato de tasa
    fija disponible (universo vacio ese dia -- no deberia pasar salvo
    caida total de un lado).

    `inflacion_esperada_rem_pct` (Epica 14 Parte 1): inflacion prox. 12m
    del REM, para el veredicto `conviene` de cada fila -- "cer" si el
    REM espera MAS inflacion que la que el par esta pricing in (el bono
    CER conviene), "tasa_fija" si espera MENOS, None sin dato de REM o
    en empate tecnico (ver UMBRAL_EMPATE_PP)."""
    universo = _universo_confiable(con)
    cer = sorted((b for b in universo if b["categoria"] == "cer"), key=lambda b: b["duration"])
    tasa_fija = [b for b in universo if b["categoria"] in CATEGORIAS_TASA_FIJA]
    rem_pct = round(inflacion_esperada_rem_pct, 2) if inflacion_esperada_rem_pct is not None else None

    resultado = []
    for b in cer:
        match = _mas_cercano_por_duration(tasa_fija, b["duration"])
        if match is None:
            continue
        breakeven_pct = round(match["tir_pct"] - b["tir_pct"], 2)
        resultado.append({
            "ticker_cer": b["ticker"],
            "duration_cer": round(b["duration"], 2),
            "tir_cer_pct": round(b["tir_pct"], 2),
            "ticker_tasa_fija": match["ticker"],
            "categoria_tasa_fija": match["categoria"],
            "categoria_tasa_fija_label": clasificar_bonos.CATEGORIA_LABEL[match["categoria"]],
            "duration_tasa_fija": round(match["duration"], 2),
            "tir_tasa_fija_pct": round(match["tir_pct"], 2),
            "brecha_duration": round(abs(b["duration"] - match["duration"]), 2),
            "breakeven_inflacion_pct": breakeven_pct,
            "inflacion_esperada_rem_pct": rem_pct,
            "conviene": _conviene(rem_pct, breakeven_pct, "cer", "tasa_fija"),
        })
    return resultado


def devaluacion_implicita(con, devaluacion_esperada_rem_pct: float | None = None) -> list[dict]:
    """1 fila por bono a tasa fija en pesos (LECAP/BONCAP), emparejado
    con el bono dollar-linked o hard-dollar (Bonares/Globales/dollar-
    linked, union de las 3 categorias) de duration mas cercana --
    `devaluacion_pct` = cuanta devaluacion esta pricing in el mercado
    (paridad de tasas, cruza moneda ARS/USD). Ordenado por duration del
    bono en pesos.

    `devaluacion_esperada_rem_pct` (Epica 14 Parte 1): devaluacion
    implicita en el tipo de cambio proyectado del REM (prox. 12m vs.
    anio actual -- ver cargar_benchmarks_macro.rem_devaluacion_pct),
    para el veredicto `conviene` de cada fila -- "usd" si el REM espera
    MAS devaluacion que la que el par esta pricing in (el dolar
    conviene), "ars" si espera MENOS, None sin dato de REM o en empate
    tecnico (ver UMBRAL_EMPATE_PP)."""
    universo = _universo_confiable(con)
    tasa_fija = sorted((b for b in universo if b["categoria"] in CATEGORIAS_TASA_FIJA), key=lambda b: b["duration"])
    dolar = [b for b in universo if b["categoria"] in CATEGORIAS_USD]
    rem_pct = round(devaluacion_esperada_rem_pct, 2) if devaluacion_esperada_rem_pct is not None else None

    resultado = []
    for b in tasa_fija:
        match = _mas_cercano_por_duration(dolar, b["duration"])
        if match is None:
            continue
        tir_ars = b["tir_pct"] / 100
        tir_usd = match["tir_pct"] / 100
        devaluacion_pct = ((1 + tir_ars) / (1 + tir_usd) - 1) * 100 if (1 + tir_usd) != 0 else None
        devaluacion_pct = round(devaluacion_pct, 2) if devaluacion_pct is not None else None
        resultado.append({
            "ticker_ars": b["ticker"],
            "duration_ars": round(b["duration"], 2),
            "tir_ars_pct": round(b["tir_pct"], 2),
            "ticker_usd": match["ticker"],
            "categoria_usd": match["categoria"],
            "categoria_usd_label": clasificar_bonos.CATEGORIA_LABEL[match["categoria"]],
            "duration_usd": round(match["duration"], 2),
            "tir_usd_pct": round(match["tir_pct"], 2),
            "brecha_duration": round(abs(b["duration"] - match["duration"]), 2),
            "devaluacion_implicita_pct": devaluacion_pct,
            "devaluacion_esperada_rem_pct": rem_pct,
            "conviene": _conviene(rem_pct, devaluacion_pct, "usd", "ars"),
        })
    return resultado
