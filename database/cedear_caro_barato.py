"""
PWOS -- "¿Que CEDEAR esta caro o barato vs. el resto?" (Fase 3 de la herramienta
de CEDEARs, 2026-09-25; idea de Sin Elefantes Blancos).

Todos los CEDEARs juntos cotizan con una prima comun contra el CCL de bonos
(el 25/09: mediana de CCL implicito ~1.623,7 vs CCL 1.621). Esa prima no dice
que CEDEAR esta caro; el desvio de CADA uno contra la mediana del mercado si:

    ccl_implicito      = ask_ars * ratio / precio_ny            # cedear_moneda.comparar_con_ny
    ccl_referencia     = mediana(ccl_implicito del set de referencia)
    desvio_vs_ref_pct  = (ccl_implicito / ccl_referencia - 1) * 100   # < 0 = mas barato que el resto
    prima_mercado_pct  = (ccl_referencia / ccl_bonos - 1) * 100
    valor_teorico_ars  = precio_ny * ccl_referencia / ratio

SET DE REFERENCIA (quienes entran en la mediana), reglas del ticket:
  1. `estado == ok` (puntas y par sanos, Fase 1) y `estado_ny == ok` (con precio
     NY y sin ratio sospechoso, Fase 2).
  2. Precio NY de data912: los ETFs con precio de yfinance (~15 min de demora)
     quedan AFUERA de la mediana; se muestran en la tabla, rotulados.
  3. Liquidez: monto operado HOY en la especie en pesos >= MIN_MONTO_OPERADO_USD
     (volumen `v` x ultimo / MEP). Medido el 2026-09-25 14:16 sobre los 325
     CEDEARs con NY ok: con umbral 0 la dispersion de los desvios es 0,38 % y 3
     papeles superan +-2 %; con USD 5.000 entran 217, la dispersion cae a 0,18 %
     y ninguno pasa +-2 %; subir el umbral (10k..100k) no cambia la mediana
     (1.623,6-1.623,8) ni la dispersion. Depende de la hora: a primera hora de
     la rueda casi nada opero -> puede quedar n < MIN_N_REFERENCIA.
  4. Si n < MIN_N_REFERENCIA (20) no se calcula referencia: se bloquea y marca.

KO es la segunda referencia (la que usa Sin Elefantes Blancos), para comparar
contra esa pagina. Solo si KO mismo entra en el set de referencia.

Fuera del horario de BYMA (cedear_moneda.fuera_de_horario) la tabla se muestra
pero SIN badges: las puntas que quedan en el libro no son precio.

BADGE solo para papeles DENTRO del set de referencia (decision del usuario,
2026-09-26): uno de afuera (poco operado, ETF de yfinance, sin punta USD...)
sigue mostrando CCL implicito y desvio, pero aunque su |desvio| supere el
umbral no lleva "caro"/"barato" -- en su lugar `aviso_sin_badge` trae el
motivo de exclusion. Un desvio grande en un papel iliquido suele ser una punta
suelta, no una oportunidad (el 25/09 los 3 badges del universo eran de afuera).

Funcion pura: recibe evaluaciones ya calculadas por cedear_moneda.evaluar_ticker.
"""
import statistics

MIN_MONTO_OPERADO_USD = 5000.0
MIN_N_REFERENCIA = 20
TICKER_REF_SECUNDARIA = "KO"


def monto_operado_usd(ev: dict, mep: float) -> float | None:
    a = ev.get("ars")
    if not a:
        return None
    precio = a.get("ultimo") or a.get("ask")
    if not precio or a.get("volumen") is None:
        return None
    return a["volumen"] * precio / mep


def motivo_exclusion(ev: dict, mep: float) -> str | None:
    """None si el CEDEAR entra en el set de referencia; si no, el motivo."""
    n = ev.get("ny") or {}
    if ev.get("estado") != "ok":
        return f"puntas: {ev.get('estado')}"
    if n.get("estado_ny") != "ok":
        return f"NY: {n.get('estado_ny')}"
    if n.get("panel_ny") == "yfinance":
        return "precio NY de yfinance (demora ~15 min)"
    monto = monto_operado_usd(ev, mep)
    if monto is None or monto < MIN_MONTO_OPERADO_USD:
        return f"poco operado hoy (USD {0 if monto is None else round(monto):,} < {MIN_MONTO_OPERADO_USD:,.0f})"
    return None


def calcular(evaluaciones: list[dict], mep: float, ccl_bonos: float | None,
             umbral_alerta_pct: float, fuera_de_horario: bool | None) -> dict:
    motivos = {ev["ticker"]: motivo_exclusion(ev, mep) for ev in evaluaciones}
    en_ref = [ev for ev in evaluaciones if motivos[ev["ticker"]] is None]
    n = len(en_ref)

    if n >= MIN_N_REFERENCIA:
        ccl_ref = statistics.median(ev["ny"]["ccl_implicito"] for ev in en_ref)
        referencia = {"estado": "ok", "ccl": ccl_ref, "n": n}
    else:
        ccl_ref = None
        referencia = {"estado": "insuficiente", "ccl": None, "n": n,
                      "motivo": f"solo {n} CEDEARs cumplen las reglas del set de referencia (minimo {MIN_N_REFERENCIA})"}

    ko = next((ev for ev in en_ref if ev["ticker"] == TICKER_REF_SECUNDARIA), None)
    ccl_ko = ko["ny"]["ccl_implicito"] if ko else None

    badges_activos = referencia["estado"] == "ok" and not fuera_de_horario
    filas = []
    for ev in evaluaciones:
        ny = ev.get("ny") or {}
        ccl_impl = ny.get("ccl_implicito")
        desvio = (ccl_impl / ccl_ref - 1) * 100 if ccl_impl and ccl_ref else None
        badge, aviso_sin_badge = None, None
        if badges_activos and desvio is not None and abs(desvio) > umbral_alerta_pct:
            if motivos[ev["ticker"]] is None:
                badge = "barato" if desvio < 0 else "caro"
            else:
                aviso_sin_badge = motivos[ev["ticker"]]
        filas.append({
            "ticker": ev["ticker"],
            "origen": ev.get("origen", []),
            "estado": ev.get("estado"),
            "estado_ny": ny.get("estado_ny"),
            "ccl_implicito": ccl_impl,
            "desvio_vs_ref_pct": desvio,
            "desvio_vs_ko_pct": (ccl_impl / ccl_ko - 1) * 100 if ccl_impl and ccl_ko else None,
            "valor_teorico_ars": (ny["precio_ny"] * ccl_ref / ny["ratio"])
                                 if ccl_ref and ny.get("precio_ny") and ny.get("ratio") else None,
            "precio_ars": (ev.get("ars") or {}).get("ask"),
            "precio_ny": ny.get("precio_ny"),
            "ticker_origen": ny.get("ticker_origen"),
            "ratio_texto": ny.get("ratio_texto"),
            "fuente_ny": ny.get("panel_ny"),
            "monto_operado_usd": monto_operado_usd(ev, mep),
            "en_referencia": motivos[ev["ticker"]] is None,
            "motivo_exclusion": motivos[ev["ticker"]],
            "badge": badge,
            "aviso_sin_badge": aviso_sin_badge,
        })

    desvios_ref = sorted(abs(f["desvio_vs_ref_pct"]) for f in filas if f["en_referencia"] and f["desvio_vs_ref_pct"] is not None)

    def pct(q):
        return desvios_ref[min(len(desvios_ref) - 1, int(len(desvios_ref) * q))] if desvios_ref else None

    return {
        "referencia": referencia,
        "ko": {"ccl": ccl_ko, "estado": "ok" if ccl_ko else "fuera_del_set",
               "motivo": None if ccl_ko else motivos.get(TICKER_REF_SECUNDARIA, "no esta en el universo")},
        "ccl_bonos": ccl_bonos,
        "prima_mercado_pct": (ccl_ref / ccl_bonos - 1) * 100 if ccl_ref and ccl_bonos else None,
        "umbral_alerta_pct": umbral_alerta_pct,
        "badges_activos": badges_activos,
        "fuera_de_horario": fuera_de_horario,
        "reglas": {"min_monto_operado_usd": MIN_MONTO_OPERADO_USD, "min_n": MIN_N_REFERENCIA,
                   "excluye_yfinance": True, "ref_secundaria": TICKER_REF_SECUNDARIA},
        # distribucion de |desvio| dentro del set, para calibrar el umbral del badge
        "distribucion_desvio_abs_pct": {"p50": pct(0.5), "p90": pct(0.9), "max": desvios_ref[-1] if desvios_ref else None},
        "filas": filas,
    }
