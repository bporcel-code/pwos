"""
Casos de prueba de cedear_moneda.py (sin red, sin base).

Los 2 casos "golden" salen de la planilla del asesor ("CEDEARS - Comprar en
dolares o pesos?"): NFLX, MEP 1.540,17. Tienen que dar exacto (+-0,01 pp).

Uso:
    python test_cedear_moneda.py
"""
from cedear_moneda import comparar_con_ny, especie_d, evaluar, evaluar_ticker, ticker_byma

MEP_PLANILLA = 1540.17
TOL_PP = 0.01


def cerca(a, b, tol=TOL_PP):
    return abs(a - b) <= tol


def test_golden_puntas():
    r = evaluar(2592.5, 1.71, MEP_PLANILLA, 1.2)
    assert r["estado"] == "ok", r
    assert cerca(r["dolar_implicito"], 1516.08), r["dolar_implicito"]
    assert cerca(r["spread_pct"], 1.59), r["spread_pct"]
    assert r["recomendacion_a"] == "pesos"
    assert r["recomendacion_b"] == "pesos_via_mep"   # 1,59 > 1,2
    assert r["recomendacion_c"] == "pesos"


def test_golden_ultimo_hoja2():
    r = evaluar(2407, 1.565, MEP_PLANILLA, 1.2)
    assert cerca(r["spread_pct"], 0.14), r["spread_pct"]
    assert r["recomendacion_a"] == "pesos"
    assert r["recomendacion_b"] == "dolares"          # 0,14 < 1,2


def test_spread_negativo():
    # especie D cara: 1,60 * 1540,17 = 2464,27 < 2500 -> conviene dolares
    r = evaluar(2500, 1.60, MEP_PLANILLA, 1.2)
    assert r["spread_pct"] < 0, r
    assert r["recomendacion_a"] == "dolares"
    assert r["recomendacion_b"] == "dolares"


def test_justo_en_el_umbral():
    # ask_ars elegido para que el spread sea EXACTAMENTE 1,2 %: la regla es
    # "mayor que", asi que en el umbral se compra directo en dolares.
    ask_usd = 1.70
    ask_ars = ask_usd * MEP_PLANILLA / 1.012
    r = evaluar(ask_ars, ask_usd, MEP_PLANILLA, 1.2)
    assert cerca(r["spread_pct"], 1.2, 1e-9), r["spread_pct"]
    assert r["recomendacion_b"] == "dolares"
    # un pelo arriba ya es pesos via MEP
    r2 = evaluar(ask_ars * 0.9999, ask_usd, MEP_PLANILLA, 1.2)
    assert r2["recomendacion_b"] == "pesos_via_mep"


def test_spread_cero_es_indiferente():
    r = evaluar(1.70 * MEP_PLANILLA, 1.70, MEP_PLANILLA, 1.2)
    assert r["recomendacion_a"] == "indiferente", r


def test_sin_punta():
    for ars, usd in ((None, 1.7), (2500, None), (0, 1.7), (2500, 0)):
        r = evaluar(ars, usd, MEP_PLANILLA, 1.2)
        assert r["estado"] == "sin_punta", r
        assert r["recomendacion_a"] is None and r["recomendacion_b"] is None and r["recomendacion_c"] is None
        assert r["spread_pct"] is None


def test_par_sospechoso():
    # caso YELP real del 2026-09-24: dolar implicito ~29 % debajo del MEP
    r = evaluar(14450, 13.16, 1541.1, 1.2)
    assert r["estado"] == "par_sospechoso", r
    assert r["recomendacion_a"] is None
    assert r["spread_pct"] is not None   # el numero se muestra, la recomendacion no


def test_ticker_y_especie_d():
    simbolos = {"NFLX", "NFLXD", "B", "B.D", "BB", "BB.D", "BBD", "BRKB", "BRKBD"}
    assert especie_d("NFLX", simbolos) == "NFLXD"
    assert especie_d("B", simbolos) == "B.D"
    assert especie_d("BB", simbolos) == "BB.D"      # BBD es Bradesco, no la D de BB
    assert especie_d("XYZ", simbolos) is None
    assert ticker_byma("b.d") == "B"
    assert ticker_byma("BRK-B") == "BRKB"
    assert ticker_byma("BBD") == "BBD"


def test_evaluar_ticker_con_panel():
    panel = {
        "NFLX": {"symbol": "NFLX", "px_bid": 2590, "px_ask": 2592.5, "q_bid": 1, "q_ask": 1, "c": 2591, "v": 1},
        "NFLXD": {"symbol": "NFLXD", "px_bid": 1.70, "px_ask": 1.71, "q_bid": 1, "q_ask": 1, "c": 1.71, "v": 1},
        "SOLO": {"symbol": "SOLO", "px_bid": 100, "px_ask": 101, "q_bid": 1, "q_ask": 1, "c": 100, "v": 1},
        "SINP": {"symbol": "SINP", "px_bid": 0, "px_ask": 0, "q_bid": 0, "q_ask": 0, "c": 2500, "v": 0},
        "SINPD": {"symbol": "SINPD", "px_bid": 1.6, "px_ask": 1.62, "q_bid": 1, "q_ask": 1, "c": 1.6, "v": 1},
    }
    r = evaluar_ticker("nflx", panel, MEP_PLANILLA, 1.2)
    assert r["estado"] == "ok" and r["especie_usd"] == "NFLXD" and cerca(r["spread_pct"], 1.59)
    assert evaluar_ticker("SOLO", panel, MEP_PLANILLA)["estado"] == "sin_especie_d"
    assert evaluar_ticker("NOEXISTE", panel, MEP_PLANILLA)["estado"] == "no_cotiza"
    # ask en 0 con ultimo operado disponible: NO se usa `c` como sustituto
    assert evaluar_ticker("SINP", panel, MEP_PLANILLA)["estado"] == "sin_punta"


# ------------------------------------------------------------ Fase 2 (vs NY)

CCL_PLANILLA = 1608.14


def test_seb_nflx_ccl_implicito():
    # Sin Elefantes Blancos 2026-09-24: NFLX US$ 71,99, $ 2.413, ratio 48 -> CCL implicito ~1.609,0
    r = comparar_con_ny(2413, None, 71.99, 48, MEP_PLANILLA, 1609.0)
    assert abs(r["ccl_implicito"] / 1609.0 - 1) <= 0.001, r["ccl_implicito"]
    # contra su propio CCL implicito la prima en pesos es ~0
    assert abs(r["prima_ars_pct"]) < 0.01, r["prima_ars_pct"]


def test_seb_ko_ccl_implicito():
    r = comparar_con_ny(28460, None, 88.51, 5, MEP_PLANILLA, 1607.8)
    assert abs(r["ccl_implicito"] / 1607.8 - 1) <= 0.001, r["ccl_implicito"]


def test_planilla_formulas():
    # Entradas del caso golden de la planilla (NFLX, ratio 48, CCL 1.608,14,
    # MEP 1.540,17) con el precio NY de SEB. Precio teorico y prima a mano:
    r = comparar_con_ny(2592.5, 1.71, 71.99, 48, MEP_PLANILLA, CCL_PLANILLA)
    assert r["estado_ny"] == "ok", r
    assert cerca(r["precio_teorico_ars"], 71.99 * 1608.14 / 48), r["precio_teorico_ars"]     # 2.411,87
    assert cerca(r["precio_teorico_usd"], 71.99 / 48 * (1608.14 / 1540.17), 1e-9)
    assert cerca(r["ny_implicito_ars"], 2592.5 * 48 / 1608.14, 1e-9)                        # 77,38
    assert cerca(r["prima_ars_pct"], (2592.5 * 48 / 1608.14 / 71.99 - 1) * 100, 1e-9)       # +7,49 %
    # identidad: (1 + prima_usd) / (1 + prima_ars) = 1 + spread (Fase 1: +1,59 %)
    spread = evaluar(2592.5, 1.71, MEP_PLANILLA)["spread_pct"]
    cociente = (1 + r["prima_usd_pct"] / 100) / (1 + r["prima_ars_pct"] / 100)
    assert cerca(cociente, 1 + spread / 100, 1e-12), (cociente, spread)


def test_control_split():
    # ratio de la tabla 24 cuando el mercado cotiza 48 (split no reflejado): se bloquea la prima
    r = comparar_con_ny(2413, 1.565, 71.99, 24, MEP_PLANILLA, 1609.0)
    assert r["estado_ny"] == "ratio_desactualizado", r
    assert r["prima_ars_pct"] is None and r["prima_usd_pct"] is None and r["ccl_implicito"] is None
    assert cerca(r["ratio_implicito"], 71.99 * 1609.0 / 2413, 1e-9)
    # 14 % de desvio todavia pasa, 16 % no
    assert comparar_con_ny(2413, None, 71.99, 48 * 1.14, MEP_PLANILLA, 1609.0)["estado_ny"] == "ok"
    assert comparar_con_ny(2413, None, 71.99, 48 * 1.19, MEP_PLANILLA, 1609.0)["estado_ny"] == "ratio_desactualizado"


def test_fraccionario():
    # ABEV: 1 CEDEAR = 3 ADRs -> ratio 1/3. ADR US$ 2,93, CCL 1.608 -> CEDEAR teorico ~$ 14.134
    r = comparar_con_ny(14150, None, 2.93, 1 / 3, MEP_PLANILLA, 1608.0)
    assert r["estado_ny"] == "ok", r
    assert cerca(r["precio_teorico_ars"], 2.93 * 1608.0 * 3, 1e-6)


def test_sin_precio_ny_y_sin_ratio():
    assert comparar_con_ny(2413, 1.5, None, 48, MEP_PLANILLA, 1609.0)["estado_ny"] == "sin_precio_ny"
    assert comparar_con_ny(2413, 1.5, 71.99, None, MEP_PLANILLA, 1609.0)["estado_ny"] == "sin_ratio"
    r = comparar_con_ny(None, 1.5, 71.99, 48, MEP_PLANILLA, 1609.0)
    assert r["estado_ny"] == "sin_punta" and r["prima_ars_pct"] is None


def test_evaluar_ticker_con_ny():
    panel = {
        "NFLX": {"symbol": "NFLX", "px_bid": 2410, "px_ask": 2413, "q_bid": 1, "q_ask": 50, "c": 2411, "v": 1},
        "NFLXD": {"symbol": "NFLXD", "px_bid": 1.56, "px_ask": 1.565, "q_bid": 1, "q_ask": 9, "c": 1.56, "v": 1},
        "SPY": {"symbol": "SPY", "px_bid": 20570, "px_ask": 20590, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
        "SPYD": {"symbol": "SPYD", "px_bid": 13.37, "px_ask": 13.38, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
    }
    ratios = {"NFLX": {"ticker_origen": "NFLX", "ratio_cedear": 48.0, "ratio_subyacente": 1.0, "ratio": 48.0,
                       "fecha_lista": "2026-09-16"},
              "SPY": {"ticker_origen": "SPY", "ratio_cedear": 20.0, "ratio_subyacente": 1.0, "ratio": 20.0,
                      "fecha_lista": "2026-09-16"}}
    ny = {"NFLX": {"precio": 71.99, "panel": "usa_stocks"}}
    r = evaluar_ticker("NFLX", panel, MEP_PLANILLA, ratios=ratios, precios_ny=ny, ccl=1609.0)
    assert r["ny"]["estado_ny"] == "ok" and r["ny"]["ratio_texto"] == "48:1" and r["ny"]["panel_ny"] == "usa_stocks"
    assert r["ars"]["q_ask"] == 50 and r["usd"]["q_ask"] == 9
    # ETF sin panel en data912: sin_precio_ny, nunca se busca en otra fuente
    assert evaluar_ticker("SPY", panel, MEP_PLANILLA, ratios=ratios, precios_ny=ny, ccl=1609.0)["ny"]["estado_ny"] == "sin_precio_ny"
    # sin ratios/NY (Fase 1 sola) no aparece la clave
    assert "ny" not in evaluar_ticker("NFLX", panel, MEP_PLANILLA)


def test_etf_con_yfinance():
    panel = {
        "SPY": {"symbol": "SPY", "px_bid": 20570, "px_ask": 20590, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
        "SPYD": {"symbol": "SPYD", "px_bid": 13.37, "px_ask": 13.38, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
        "MELI": {"symbol": "MELI", "px_bid": 23500, "px_ask": 23520, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
        "MELID": {"symbol": "MELID", "px_bid": 15.2, "px_ask": 15.21, "q_bid": 1, "q_ask": 1, "c": 1, "v": 1},
    }
    fila = {"ratio_cedear": 20.0, "ratio_subyacente": 1.0, "ratio": 20.0, "fecha_lista": "2026-09-16"}
    ratios = {"SPY": {**fila, "ticker_origen": "SPY", "tipo": "etf"},
              "MELI": {**fila, "ticker_origen": "MELI", "ratio_cedear": 120.0, "ratio": 120.0, "tipo": "accion"}}
    pedidos = []

    def fake_yf(t):
        pedidos.append(t)
        return 256.0   # coherente con ratio 20: 256 * 1609 / 20 = 20.595

    r = evaluar_ticker("SPY", panel, MEP_PLANILLA, ratios=ratios, precios_ny={}, ccl=1609.0, precio_etf=fake_yf)
    assert r["ny"]["estado_ny"] == "ok" and r["ny"]["panel_ny"] == "yfinance" and r["ny"]["precio_ny"] == 256.0
    # una accion ausente de data912 NO va a yfinance: sin_precio_ny
    r = evaluar_ticker("MELI", panel, MEP_PLANILLA, ratios=ratios, precios_ny={}, ccl=1609.0, precio_etf=fake_yf)
    assert r["ny"]["estado_ny"] == "sin_precio_ny"
    assert pedidos == ["SPY"], pedidos
    # si Yahoo no responde, sin_precio_ny
    r = evaluar_ticker("SPY", panel, MEP_PLANILLA, ratios=ratios, precios_ny={}, ccl=1609.0, precio_etf=lambda t: None)
    assert r["ny"]["estado_ny"] == "sin_precio_ny"
    # si data912 lo tiene, manda data912 aunque sea ETF
    r = evaluar_ticker("SPY", panel, MEP_PLANILLA, ratios=ratios, precios_ny={"SPY": {"precio": 256.5, "panel": "usa_stocks"}},
                       ccl=1609.0, precio_etf=fake_yf)
    assert r["ny"]["panel_ny"] == "usa_stocks" and pedidos == ["SPY"]


def test_loader_ebr_axia():
    from cargar_ratios_cedear import armar_filas
    byma = [{"nombre": "Centrais Electricas Brasileiras", "codigo": "EBR", "mercado": "NYSE", "ratio_texto": "1:4", "pagina": 1}]
    base = {"ticker": "AXIAY", "nombre": "AXIA ENERGIA", "marca": "**", "ratio_texto": "1 :4", "isin_cedear": "X",
            "mercado": "OTC US", "tipo": "adr", "tipo_raw": "ADR", "pais": "Brasil", "industria": "Utilities"}
    filas, rep = armar_filas(byma, {"AXIAY": {**base, "isin_subyacente": "US15234Q2075"}}, "2026-09-16", "2026-09-17")
    assert filas[0]["ticker_origen"] == "AXIA" and filas[0]["emisor"] == "comafi" and not rep["isin_distinto"]
    filas, rep = armar_filas(byma, {"AXIAY": {**base, "isin_subyacente": "US0000000000"}}, "2026-09-16", "2026-09-17")
    assert filas[0]["emisor"] is None and len(rep["isin_distinto"]) == 1


def test_fuera_de_horario():
    from cedear_moneda import fuera_de_horario
    assert fuera_de_horario("2026-09-24T15:42:41-03:00") is False     # jueves en rueda
    assert fuera_de_horario("2026-09-24T17:47:49-03:00") is True      # jueves despues del cierre
    assert fuera_de_horario("2026-09-24T10:59:00-03:00") is True
    assert fuera_de_horario("2026-09-26T13:00:00-03:00") is True      # sabado
    assert fuera_de_horario("2026-09-24T18:00:00+00:00") is False     # 15:00 en Argentina
    assert fuera_de_horario(None) is None


def test_golden_planilla_relacion_arg_eeuu():
    # Hoja `Relacion ARG-EEUU` de la planilla del asesor (valores provistos por
    # el usuario 2026-09-25): NFLX, precio NY 71,90, ratio 48, CCL 1.608,14, MEP 1.540,17.
    ny, ratio = 71.90, 48
    # con el ultimo operado (ARS 2.407 / USD 1,565)
    r = comparar_con_ny(2407, 1.565, ny, ratio, MEP_PLANILLA, CCL_PLANILLA)
    assert cerca(r["precio_teorico_ars"], 2408.86, 0.005), r["precio_teorico_ars"]
    assert cerca(r["precio_teorico_usd"], 1.5640, 0.00005), r["precio_teorico_usd"]
    assert cerca(r["ny_implicito_ars"], 71.84, 0.005), r["ny_implicito_ars"]
    assert cerca(r["ny_implicito_usd"], 71.94, 0.005), r["ny_implicito_usd"]
    # con las puntas (ARS 2.592,5 / USD 1,71)
    r = comparar_con_ny(2592.5, 1.71, ny, ratio, MEP_PLANILLA, CCL_PLANILLA)
    assert cerca(r["ny_implicito_ars"], 77.38, 0.005), r["ny_implicito_ars"]
    assert cerca(r["ny_implicito_usd"], 78.611, 0.0005), r["ny_implicito_usd"]


def test_yfinance_timeout_y_cache_tardio(monkeypatch):
    import time
    import cedear_moneda as cm
    import configuracion

    # Camino del modo real: en modo demo el precio sale de la foto del cierre.
    monkeypatch.setattr(configuracion, "es_demo", lambda: False)

    original, timeout_original = cm._last_price_yf, cm.TIMEOUT_YF_SEG
    try:
        cm._CACHE_YF.clear()
        cm.TIMEOUT_YF_SEG = 0.2
        cm._last_price_yf = lambda t: (time.sleep(0.6), 123.4)[1]      # Yahoo lento
        inicio = time.monotonic()
        assert cm.precio_etf_yfinance("LENTO") is None                # no espera mas que el timeout
        assert time.monotonic() - inicio < 0.5
        time.sleep(0.8)
        assert cm.precio_etf_yfinance("LENTO") == 123.4               # llego tarde y quedo en cache
        cm._last_price_yf = lambda t: (_ for _ in ()).throw(RuntimeError("rate limit"))
        assert cm.precio_etf_yfinance("FALLA") is None                # error -> None, sin excepcion
    finally:
        cm._last_price_yf, cm.TIMEOUT_YF_SEG = original, timeout_original
        cm._CACHE_YF.clear()


def main():
    pruebas = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for p in pruebas:
        p()
        print(f"  OK  {p.__name__}")
    print(f"\nOK -- {len(pruebas)} casos de prueba de cedear_moneda.py")


if __name__ == "__main__":
    main()
