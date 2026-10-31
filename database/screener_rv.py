"""
PWOS -- Screener Graham-Plus sobre cartera + watchlist (Epica 19, Fase 3,
2026-09-21). Arma las filas con las compuertas de graham_rv.py y agrega C3
(encaje), que necesita las tenencias; ademas la valuacion detallada de UN ticker
para la Ficha de posicion.

Compuertas (cada una: pasa / no_pasa / sin_verificar, nunca "pasa" por falta de dato):
  C0 Descartes duros cuantitativos (deuda neta/EBITDA > 3, cobertura < 5, FCF
     negativo sostenido). "Fuera del circulo de competencia" es un juicio tuyo:
     recordatorio, no bloquea.
  C1 Calidad (ROIC >= 12 %, margenes estables, conversion a caja).
  C2 Precio (criterios de precio de Graham-Plus de la cubeta).
  C3 Encaje: tope de posicion y de sector, prima del CEDEAR y correlacion. La
     prima del CEDEAR no se puede verificar (no existe el ratio en el sistema) y la
     correlacion llega en la Fase 4: C3 solo puede dar no_pasa (un tope violado)
     o sin_verificar.

ETF (ej. SPY): "no aplica" en todo, distinto de "sin verificar", y fuera del ranking.
"""
from graham_rv import (
    CUBETAS, _criterio as criterio, compuerta_c0, compuerta_c1, compuerta_c2, leer_fundamentales,
    leer_tesis_valuacion, tabla_estricto, tabla_plus, valuacion, veredicto,
)
from parametros_rv import leer_parametros

NOTA_ESTADOS_ARS = "Estados en pesos, ajustados por inflación: tomá los datos con cautela."


def _tipo_de_candidata(ticker: str) -> str | None:
    return "accion" if ticker.endswith(".BA") else None


def _c3(p: dict | None, f: dict | None, tipo: str | None, sector_pct: dict, topes: dict) -> dict:
    crit = []
    if p is not None:
        fallo = p["supera_tope_rv"] or p["supera_tope_patrimonio"]
        crit.append(criterio(
            "tope_posicion", "Tope de posición",
            f"≤ {topes['rv']:g} % de la RV y ≤ {topes['patrimonio']:g} % del patrimonio",
            "no_pasa" if fallo else "pasa", p["pct_rv"],
            f"{p['pct_rv']:.1f} % de la RV · {p['pct_patrimonio']:.2f} % del patrimonio" if p.get("pct_patrimonio") is not None else None))
    else:
        crit.append(criterio("tope_posicion", "Tope de posición", "—", "no_aplica", None, "todavía no es una posición"))

    sector = f.get("sector") if f else None
    if sector is None:
        crit.append(criterio("tope_sector", "Tope de sector", f"< {topes['sector']:g} % de la RV", "sin_verificar", None, "sector sin clasificar"))
    else:
        peso = sector_pct.get(sector, 0.0)
        # cartera: ya esta dentro del peso del sector; candidata: tiene que quedar lugar
        ok = peso <= topes["sector"] if p is not None else peso < topes["sector"]
        crit.append(criterio("tope_sector", "Tope de sector", f"{'≤' if p is not None else '<'} {topes['sector']:g} % de la RV", "pasa" if ok else "no_pasa",
                             round(peso, 2), f"{sector}: {peso:.1f} % de la RV hoy"))

    if tipo == "accion":
        crit.append(criterio("prima_cedear", "Prima del CEDEAR", "—", "no_aplica", None, "acción local"))
    else:
        crit.append(criterio("prima_cedear", "Prima del CEDEAR", "vs. subyacente", "sin_verificar", None,
                             "el sistema no tiene el ratio del CEDEAR (sin verificar)"))
    crit.append(criterio("correlacion", "Correlación con la cartera", "< 0,85", "sin_verificar", None,
                         "llega con el análisis de riesgo (Fase 4)"))
    return {"estado": veredicto(crit), "criterios": crit}


def _gates_sin_datos(motivo: str) -> dict:
    g = {"estado": "sin_verificar", "criterios": [], "nota": motivo}
    return {"c0": dict(g), "c1": dict(g), "c2": dict(g), "c3": dict(g)}


def _gates_no_aplica(motivo: str) -> dict:
    g = {"estado": "no_aplica", "criterios": [], "nota": motivo}
    return {"c0": dict(g), "c1": dict(g), "c2": dict(g), "c3": dict(g)}


def screener(con, tenencias: dict) -> dict:
    """Filas de cartera (posiciones abiertas de `tenencias`) + watchlist, y el Top 5
    por margen de seguridad."""
    params = leer_parametros(con)
    topes = {"rv": params["tope_posicion_rv_pct"], "patrimonio": params["tope_posicion_patrimonio_pct"],
             "sector": params["tope_sector_rv_pct"]}
    fundamentales = leer_fundamentales(con)
    tesis = leer_tesis_valuacion(con)
    posiciones = {p["ticker"]: p for p in tenencias["posiciones"]}

    sector_pct: dict[str, float] = {}
    for p in tenencias["posiciones"]:
        s = (fundamentales.get(p["ticker"]) or {}).get("sector")
        if s:
            sector_pct[s] = sector_pct.get(s, 0.0) + p["pct_rv"]

    entradas = [(t, "cartera", p["tipo"], p["cubeta"], tesis.get(t), p) for t, p in posiciones.items()]
    for t, cubeta_sug, nombre in con.execute("SELECT ticker, cubeta_sugerida, nombre FROM watchlist_rv ORDER BY ticker;"):
        if t not in posiciones:
            entradas.append((t, "watchlist", _tipo_de_candidata(t), cubeta_sug, None, None))

    filas = []
    for ticker, origen, tipo, cubeta, tes, p in entradas:
        f = fundamentales.get(ticker)
        val = valuacion(f, tes, cubeta)
        fila = {
            "ticker": ticker, "origen": origen, "tipo": tipo, "cubeta": cubeta,
            "nombre": (f or {}).get("nombre"), "fecha_dato": (f or {}).get("fecha_dato"),
            # "la fila existe" != "hay datos utiles" -- cargar_fundamentales_rv.py
            # siempre escribe una fila, hasta para un ticker que Yahoo no
            # resuelve (todo NULL). `tipo_instrumento` solo se completa tras
            # resolver Y pasar el cross-check de nombre (ver mas abajo).
            "tiene_fundamentales": bool(f and f.get("tipo_instrumento") is not None), "no_aplica": False,
            "pct_rv": p["pct_rv"] if p else None,
            "estado_valor": val["estado"], "margen_pct": val["margen_actual_pct"], "precio_usd": val["precio_usd"],
            "vi_conservador": val["vi_conservador"], "vi_origen": val["vi_origen"], "margen_req_pct": val["margen_req_pct"],
            "motivo_valor": val["motivo"],
        }
        if f is None:
            fila["gates"] = _gates_sin_datos("sin fundamentales cargados: usá 'Actualizar fundamentales'")
        elif f.get("tipo_instrumento") == "etf":
            fila["no_aplica"] = True
            fila["gates"] = _gates_no_aplica("ETF: no tiene fundamentales de empresa")
        elif f.get("tipo_instrumento") is None:
            fila["gates"] = _gates_sin_datos((f["nv"].get("_todo") or {}).get("detalle") or "Yahoo no devolvió datos utilizables")
        else:
            fila["gates"] = {"c0": compuerta_c0(f), "c1": compuerta_c1(f), "c2": compuerta_c2(f, cubeta),
                             "c3": _c3(p, f, tipo, sector_pct, topes)}
        filas.append(fila)

    ranking = sorted((x for x in filas if x["margen_pct"] is not None and not x["no_aplica"]), key=lambda x: -x["margen_pct"])
    top5 = [{k: x[k] for k in ("ticker", "origen", "margen_pct", "estado_valor", "precio_usd", "vi_conservador")} for x in ranking[:5]]
    return {"filas": filas, "top5": top5, "topes": topes, "con_margen": len(ranking),
            "sin_fundamentales": sorted(x["ticker"] for x in filas if not x["tiene_fundamentales"])}


def valuacion_de_ticker(con, ticker: str) -> dict:
    """Detalle para la Ficha: tablas Graham estricto vs Graham-Plus, valor intrinseco y
    estado de valor. LookupError si el ticker no es de `activos` ni de la watchlist."""
    en_activos = con.execute("SELECT 1 FROM activos WHERE ticker = ? AND tipo IN ('accion', 'cedear');", (ticker,)).fetchone()
    wl = con.execute("SELECT cubeta_sugerida FROM watchlist_rv WHERE ticker = ?;", (ticker,)).fetchone()
    if not en_activos and not wl:
        raise LookupError(f"{ticker!r} no es una acción/CEDEAR de tus activos ni de la watchlist")
    f = leer_fundamentales(con).get(ticker)
    tes = leer_tesis_valuacion(con).get(ticker)
    cubeta = (tes or {}).get("cubeta") or (wl[0] if wl else None)
    val = valuacion(f, tes, cubeta)
    out = {"ticker": ticker, "cubeta": cubeta, "tiene_fundamentales": bool(f and f.get("tipo_instrumento") is not None),
           "valuacion": val, "notas": []}
    if f is None:
        out["motivo"] = "sin fundamentales cargados: usá 'Actualizar fundamentales'"
        return out
    out.update({k: f.get(k) for k in ("nombre", "tipo_instrumento", "sector", "industria", "fecha_dato", "fuente",
                                      "moneda_cotizacion", "moneda_estados", "anios_estados")})
    out["campos_no_verificados"] = f["nv"]
    out["racha_dividendos"] = (f["series"] or {}).get("racha_dividendos")
    if f.get("tipo_instrumento") != "empresa":
        out["motivo"] = (f["nv"].get("_todo") or {}).get("detalle") or "sin datos de empresa"
        return out
    estricto = tabla_estricto(f)
    plus, nota_plus = tabla_plus(f, cubeta)
    out["estricto"] = {"criterios": estricto, "veredicto": veredicto(estricto)}
    out["plus"] = {"criterios": plus, "veredicto": veredicto(plus) if plus else "sin_verificar", "nota": nota_plus}
    if f.get("moneda_estados") == "ARS":
        out["notas"].append(NOTA_ESTADOS_ARS)
    if f.get("moneda_cotizacion") and f.get("moneda_estados") and f["moneda_cotizacion"] != f["moneda_estados"]:
        out["notas"].append(f"Cotiza en {f['moneda_cotizacion']} y reporta en {f['moneda_estados']}: los montos se llevaron a USD al MEP.")
    return out
