"""
Casos de prueba de cedear_caro_barato.py (sin red, sin base). Las evaluaciones
se arman con cedear_moneda.evaluar_ticker sobre paneles sinteticos, asi se
prueba la integracion real entre las dos fases.

Uso:
    python test_cedear_caro_barato.py
"""
import statistics

from cedear_caro_barato import MIN_N_REFERENCIA, calcular
from cedear_moneda import evaluar_ticker

MEP, CCL = 1555.0, 1621.0


def armar(tickers_ccl: dict[str, float], *, volumen=10_000, etf_yf=(), sin_ny=()):
    """Un CEDEAR por ticker, ratio 10, precio NY 100 y ask_ars tal que su CCL
    implicito sea el pedido (ask_ars = ccl * 100 / 10)."""
    panel, ratios, ny = {}, {}, {}
    for t, ccl_impl in tickers_ccl.items():
        ask = ccl_impl * 10
        panel[t] = {"symbol": t, "px_bid": ask - 1, "px_ask": ask, "q_bid": 1, "q_ask": 100, "c": ask, "v": volumen}
        panel[t + "D"] = {"symbol": t + "D", "px_bid": ask / MEP - 0.01, "px_ask": ask / MEP, "q_bid": 1, "q_ask": 100,
                          "c": ask / MEP, "v": 1}
        ratios[t] = {"ticker_origen": t, "ratio_cedear": 10.0, "ratio_subyacente": 1.0, "ratio": 10.0,
                     "fecha_lista": "2026-09-16", "tipo": "etf" if t in etf_yf else "accion"}
        if t not in etf_yf and t not in sin_ny:
            ny[t] = {"precio": 100.0, "panel": "usa_stocks"}
    evs = []
    for t in tickers_ccl:
        ev = evaluar_ticker(t, panel, MEP, ratios=ratios, precios_ny=ny, ccl=CCL,
                            precio_etf=(lambda _t: 100.0) if t in etf_yf else None)
        ev["origen"] = []
        evs.append(ev)
    return evs


def universo(n=30, base=1620.0):
    return {f"T{i:02d}": base + i * 0.1 for i in range(n)}


def test_sin_elefantes_blancos():
    # 24/09: NFLX US$ 71,99 / $ 2.413 / 48 -> 1.609,0; KO US$ 88,51 / $ 28.460 / 5 -> 1.607,8 (+-0,1 %)
    panel = {"NFLX": {"symbol": "NFLX", "px_bid": 2412, "px_ask": 2413, "q_bid": 1, "q_ask": 1, "c": 2413, "v": 1},
             "KO": {"symbol": "KO", "px_bid": 28450, "px_ask": 28460, "q_bid": 1, "q_ask": 1, "c": 28460, "v": 1}}
    ratios = {"NFLX": {"ticker_origen": "NFLX", "ratio_cedear": 48.0, "ratio_subyacente": 1.0, "ratio": 48.0,
                       "fecha_lista": "x", "tipo": "accion"},
              "KO": {"ticker_origen": "KO", "ratio_cedear": 5.0, "ratio_subyacente": 1.0, "ratio": 5.0,
                     "fecha_lista": "x", "tipo": "accion"}}
    ny = {"NFLX": {"precio": 71.99, "panel": "usa_stocks"}, "KO": {"precio": 88.51, "panel": "usa_stocks"}}
    for t, esperado in (("NFLX", 1609.0), ("KO", 1607.8)):
        ev = evaluar_ticker(t, panel, MEP, ratios=ratios, precios_ny=ny, ccl=1609.0)
        assert abs(ev["ny"]["ccl_implicito"] / esperado - 1) <= 0.001, (t, ev["ny"]["ccl_implicito"])


def test_mediana_desvio_valor_teorico_y_ko():
    ccls = universo()
    ccls["KO"] = 1622.0
    ccls["BARATO"] = 1580.0     # -2,5 % vs ~1.621,5
    ccls["CARO"] = 1660.0       # +2,4 %
    r = calcular(armar(ccls), MEP, CCL, 2.0, False)
    assert r["referencia"]["estado"] == "ok" and r["referencia"]["n"] == 33
    med = statistics.median(ccls.values())
    assert abs(r["referencia"]["ccl"] - med) < 1e-6
    assert abs(r["prima_mercado_pct"] - (med / CCL - 1) * 100) < 1e-9
    f = {x["ticker"]: x for x in r["filas"]}
    assert f["BARATO"]["badge"] == "barato" and f["CARO"]["badge"] == "caro" and f["T05"]["badge"] is None
    assert abs(f["BARATO"]["desvio_vs_ref_pct"] - (1580 / med - 1) * 100) < 1e-9
    assert abs(f["BARATO"]["desvio_vs_ko_pct"] - (1580 / 1622 - 1) * 100) < 1e-9
    assert abs(f["BARATO"]["valor_teorico_ars"] - 100 * med / 10) < 1e-6
    assert r["ko"]["estado"] == "ok"


def test_n_menor_a_20_bloquea():
    r = calcular(armar(universo(MIN_N_REFERENCIA - 1)), MEP, CCL, 2.0, False)
    assert r["referencia"]["estado"] == "insuficiente" and r["referencia"]["ccl"] is None
    assert all(f["badge"] is None and f["desvio_vs_ref_pct"] is None for f in r["filas"])
    assert r["prima_mercado_pct"] is None
    # justo 20 alcanza
    assert calcular(armar(universo(MIN_N_REFERENCIA)), MEP, CCL, 2.0, False)["referencia"]["estado"] == "ok"


def test_etf_yfinance_fuera_de_la_referencia_pero_en_la_tabla():
    ccls = universo()
    ccls["SPY"] = 1700.0          # si entrara, correria la mediana
    r = calcular(armar(ccls, etf_yf=("SPY",)), MEP, CCL, 2.0, False)
    spy = next(f for f in r["filas"] if f["ticker"] == "SPY")
    assert not spy["en_referencia"] and "yfinance" in spy["motivo_exclusion"]
    assert spy["fuente_ny"] == "yfinance" and spy["desvio_vs_ref_pct"] is not None
    assert spy["badge"] is None and "yfinance" in spy["aviso_sin_badge"]     # fuera del set: sin badge
    assert r["referencia"]["n"] == 30
    assert abs(r["referencia"]["ccl"] - statistics.median(universo().values())) < 1e-6


def test_liquidez_minima():
    # volumen 1 nominal a ~$16.000 = ~USD 10: nadie entra -> bloqueado
    r = calcular(armar(universo(), volumen=1), MEP, CCL, 2.0, False)
    assert r["referencia"]["estado"] == "insuficiente"
    assert "poco operado" in r["filas"][0]["motivo_exclusion"]


def test_sin_precio_ny_y_ko_ausente():
    ccls = universo()
    ccls["NONY"] = 1600.0
    r = calcular(armar(ccls, sin_ny=("NONY",)), MEP, CCL, 2.0, False)
    nony = next(f for f in r["filas"] if f["ticker"] == "NONY")
    assert nony["estado_ny"] == "sin_precio_ny" and nony["ccl_implicito"] is None and not nony["en_referencia"]
    assert r["ko"]["estado"] == "fuera_del_set" and all(f["desvio_vs_ko_pct"] is None for f in r["filas"])


def test_fuera_de_horario_sin_badges():
    ccls = universo()
    ccls["BARATO"] = 1500.0
    r = calcular(armar(ccls), MEP, CCL, 2.0, True)
    assert r["referencia"]["estado"] == "ok" and not r["badges_activos"]
    assert all(f["badge"] is None for f in r["filas"])
    assert next(f for f in r["filas"] if f["ticker"] == "BARATO")["desvio_vs_ref_pct"] < -2


def test_fuera_del_set_con_desvio_grande_sin_badge():
    ccls = universo()
    ccls["ILIQ"] = 1500.0          # -7,5 % vs la mediana, muy por encima del umbral
    evs = armar(ccls)
    iliq = next(ev for ev in evs if ev["ticker"] == "ILIQ")
    iliq["ars"]["volumen"] = 1     # ~USD 10 operados hoy: queda fuera del set
    r = calcular(evs, MEP, CCL, 2.0, False)
    f = next(x for x in r["filas"] if x["ticker"] == "ILIQ")
    assert not f["en_referencia"] and f["desvio_vs_ref_pct"] < -2 and f["ccl_implicito"] is not None
    assert f["badge"] is None
    assert "poco operado" in f["aviso_sin_badge"]
    # un papel del set dentro del umbral no lleva ni badge ni aviso
    t00 = next(x for x in r["filas"] if x["ticker"] == "T00")
    assert t00["badge"] is None and t00["aviso_sin_badge"] is None


def main():
    pruebas = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for p in pruebas:
        p()
        print(f"  OK  {p.__name__}")
    print(f"\nOK -- {len(pruebas)} casos de prueba de cedear_caro_barato.py")


if __name__ == "__main__":
    main()
