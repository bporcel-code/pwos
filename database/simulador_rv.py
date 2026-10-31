"""
PWOS -- Simulador de compra de Renta Variable (Epica 19, Fase 4, 2026-09-22).

Modulo PURO: solo lee de la base. `fundamentales_rv`/`precios_candidatas`
del candidato deben existir ANTES de llamar a `simular()` -- api.py las
asegura via subprocess a cargar_fundamentales_rv.py/cargar_precios_rv.py
(mismo patron que el resto de los refrescos a demanda, "si hace falta":
solo si el candidato no tiene datos todavia). Sin esos datos, `simular()`
igual responde (pesos antes/despues no los necesitan), pero la
correlacion/riesgo quedan con `confianza: "insuficiente"`.

Herramienta de DIAGNOSTICO, no de decision: muestra pesos, correlacion,
riesgo y topes ANTES/DESPUES de una compra hipotetica. NUNCA dice
"comprar" ni "vender" -- la "lectura en texto" son reglas mecanicas del
marco del usuario, no una recomendacion. NO PERSISTE NADA por si solo: el
guardado en `simulaciones_compra_rv` (v2.34) es explicito, lo dispara el
caller (api.py) solo si el body pide `guardar: true`.

Frontera eficiente ANTES/DESPUES (parte de la seccion 7.3 del ticket): el
metodo en si (covarianza con shrinkage Ledoit-Wolf a mano, restricciones
de peso) es el contenido completo de la Fase 5, todavia no existe --
`frontera` viene con `disponible: False` y el motivo explicito, nunca
simulado con algo improvisado.
"""
import json

import pandas as pd

from correlacion_rv import _posiciones_vigentes
from graham_rv import leer_fundamentales
from parametros_rv import leer_parametros
from retornos_rv import (
    MIN_MESES_ALTA_CONFIANZA,
    UMBRAL_REDUNDANCIA,
    construir,
    correlacion_ponderada,
    matriz_correlacion,
    riesgo_cartera,
)

FUENTE_APORTE_NUEVO = "aporte_nuevo"


def _pct_por_sector(posiciones: list[dict], campo_pct: str = "pct_rv") -> dict[str, float]:
    out: dict[str, float] = {}
    for p in posiciones:
        s = p.get("sector")
        if s:
            out[s] = out.get(s, 0.0) + p[campo_pct]
    return out


def _pct_por_cubeta(posiciones: list[dict], campo_pct: str = "pct_rv") -> dict[str, float]:
    out: dict[str, float] = {}
    for p in posiciones:
        c = p.get("cubeta") or "sin_asignar"
        out[c] = out.get(c, 0.0) + p[campo_pct]
    return out


def _topes_violados(pct_rv: float, pct_patrimonio: float | None, sector: str | None,
                    pct_sector: float, params: dict) -> list[str]:
    violados = []
    if pct_rv > params["tope_posicion_rv_pct"]:
        violados.append("posición (% de la RV)")
    if pct_patrimonio is not None and pct_patrimonio > params["tope_posicion_patrimonio_pct"]:
        violados.append("posición (% del patrimonio)")
    if sector and pct_sector > params["tope_sector_rv_pct"]:
        violados.append(f"sector ({sector})")
    return violados


def validar_entrada(tenencias: dict, monto_usd: float, fuente_fondos: str) -> dict | None:
    """Las 2 validaciones BARATAS (no dependen del candidato, no tocan la red):
    monto > 0 y, si se vende de una posicion, que esa posicion exista y
    alcance. api.py las llama ANTES de asegurar los datos del candidato --
    sin esto, un pedido invalido (ej. vender mas de lo que hay) igual
    gastaba ~7s pegandole a Yahoo antes de fallar. Devuelve la posicion de
    la que se vende (o None si es aporte_nuevo); lanza ValueError si no
    pasa. `simular()` vuelve a correr esta misma validacion (defensa en
    profundidad, sigue siendo una funcion autocontenida)."""
    if monto_usd is None or monto_usd <= 0:
        raise ValueError("El monto tiene que ser mayor a 0.")
    if fuente_fondos == FUENTE_APORTE_NUEVO:
        return None
    vendiendo_de = {p["ticker"]: p for p in tenencias["posiciones"]}.get(fuente_fondos)
    if vendiendo_de is None:
        raise ValueError(f"'{fuente_fondos}' no es una posición abierta de tu cartera.")
    if monto_usd > vendiendo_de["valor_usd"]:
        raise ValueError(
            f"No podés vender USD {monto_usd:,.2f} de {fuente_fondos}: la posición vale USD {vendiendo_de['valor_usd']:,.2f}."
        )
    return vendiendo_de


def simular(con, tenencias: dict, ticker: str, monto_usd: float, fuente_fondos: str,
           cubeta: str | None = None, sector: str | None = None) -> dict:
    """`tenencias`: la respuesta YA CALCULADA de tenencias_rv.tenencias_rv()
    (no se vuelve a valorizar nada). `ticker`: ya normalizado por el
    caller. Lanza ValueError (400 en api.py) ante un input invalido."""
    vendiendo_de = validar_entrada(tenencias, monto_usd, fuente_fondos)

    posiciones_antes = tenencias["posiciones"]
    por_ticker_antes = {p["ticker"]: p for p in posiciones_antes}
    total_rv_antes = tenencias["kpis"]["valor_total_usd"]
    patrimonio_antes = tenencias["patrimonio_bruto_usd"]

    # --- cubeta / sector del candidato: override > watchlist/fundamentales > None ---
    fila_watchlist = con.execute(
        "SELECT cubeta_sugerida FROM watchlist_rv WHERE ticker = ?;", (ticker,)
    ).fetchone()
    fundamentales = leer_fundamentales(con).get(ticker)
    cubeta = cubeta or (fila_watchlist[0] if fila_watchlist else None) or (por_ticker_antes.get(ticker, {}).get("cubeta"))
    sector = sector or (fundamentales.get("sector") if fundamentales else None) or (por_ticker_antes.get(ticker, {}).get("sector"))

    # --- totales despues ---
    es_posicion_existente = ticker in por_ticker_antes
    if fuente_fondos == FUENTE_APORTE_NUEVO:
        total_rv_despues = total_rv_antes + monto_usd
        patrimonio_despues = patrimonio_antes + monto_usd
    else:
        total_rv_despues = total_rv_antes
        patrimonio_despues = patrimonio_antes

    # --- posiciones despues (existentes + candidato, valor_usd primero) ---
    valor_despues: dict[str, float] = {p["ticker"]: p["valor_usd"] for p in posiciones_antes}
    if vendiendo_de is not None:
        valor_despues[vendiendo_de["ticker"]] -= monto_usd
    valor_despues[ticker] = valor_despues.get(ticker, 0.0) + monto_usd

    posiciones_despues = []
    for t, valor in valor_despues.items():
        antes = por_ticker_antes.get(t)
        posiciones_despues.append({
            "ticker": t,
            "es_candidato": t == ticker and not es_posicion_existente,
            "cubeta": cubeta if t == ticker else (antes or {}).get("cubeta"),
            "sector": sector if t == ticker else (antes or {}).get("sector"),
            "valor_usd_antes": (antes or {}).get("valor_usd", 0.0),  # 0.0 si es un candidato nuevo (sin fila en `antes`)
            "valor_usd_despues": round(valor, 2),
            "pct_rv_antes": (antes or {}).get("pct_rv", 0.0),
            "pct_rv_despues": round(valor / total_rv_despues * 100, 2) if total_rv_despues else 0.0,
            "pct_patrimonio_antes": (antes or {}).get("pct_patrimonio"),
            "pct_patrimonio_despues": round(valor / patrimonio_despues * 100, 2) if patrimonio_despues else None,
        })
    posiciones_despues.sort(key=lambda p: -p["pct_rv_despues"])

    params = leer_parametros(con)
    sector_antes_pct = _pct_por_sector(posiciones_antes)
    sector_despues_pct = _pct_por_sector(posiciones_despues, "pct_rv_despues")
    for p in posiciones_despues:
        s = p["sector"]
        p["topes_violados_antes"] = _topes_violados(
            p["pct_rv_antes"], p["pct_patrimonio_antes"], s, sector_antes_pct.get(s, 0.0), params
        ) if p["ticker"] in por_ticker_antes else []
        p["topes_violados_despues"] = _topes_violados(
            p["pct_rv_despues"], p["pct_patrimonio_despues"], s, sector_despues_pct.get(s, 0.0), params
        )
        p["topes_nuevos"] = [t for t in p["topes_violados_despues"] if t not in p["topes_violados_antes"]]

    cubeta_antes = _pct_por_cubeta(posiciones_antes)
    cubeta_despues = _pct_por_cubeta(posiciones_despues, "pct_rv_despues")
    cubetas = {
        c: {"pct_antes": round(cubeta_antes.get(c, 0.0), 2), "pct_despues": round(cubeta_despues.get(c, 0.0), 2)}
        for c in sorted(set(cubeta_antes) | set(cubeta_despues))
    }
    sectores = {
        s: {"pct_antes": round(sector_antes_pct.get(s, 0.0), 2), "pct_despues": round(sector_despues_pct.get(s, 0.0), 2),
            "supera_tope_despues": sector_despues_pct.get(s, 0.0) > params["tope_sector_rv_pct"]}
        for s in sorted(set(sector_antes_pct) | set(sector_despues_pct))
    }

    # --- correlacion y riesgo: retornos semanales de las vigentes + el candidato ---
    vigentes = _posiciones_vigentes(con)
    tickers_vigentes = {t for t, _ in vigentes}
    universo = list(vigentes)
    candidato_es_nuevo = ticker not in tickers_vigentes
    if candidato_es_nuevo:
        universo.append((ticker, "candidata"))
    r = construir(con, universo)
    corr = matriz_correlacion(r.retornos)

    pesos_antes = {p["ticker"]: p["pct_rv"] / 100 for p in posiciones_antes}
    pesos_despues = {p["ticker"]: p["pct_rv_despues"] / 100 for p in posiciones_despues}

    confianza = "alta"
    corr_candidato, redundante_con, corr_media_ponderada = [], [], None
    if ticker in r.excluidos:
        confianza = "insuficiente"
    else:
        if ticker in r.confianza_baja:
            confianza = "baja"
        if ticker in corr.columns:
            fila = corr[ticker]
            for t, v in fila.items():
                if t == ticker or pd.isna(v):
                    continue
                corr_candidato.append({"ticker": t, "correlacion": round(float(v), 3)})
            corr_candidato.sort(key=lambda x: -x["correlacion"])
            redundante_con = [c for c in corr_candidato if c["correlacion"] >= UMBRAL_REDUNDANCIA]
            corr_media_ponderada = correlacion_ponderada(fila, pesos_antes)

    cols_sin_candidato = [c for c in r.retornos.columns if not (candidato_es_nuevo and c == ticker)]
    riesgo_antes = riesgo_cartera(r.retornos[cols_sin_candidato], pesos_antes)
    if ticker in r.excluidos:
        # Sin retornos del candidato no se puede estimar el impacto -- None
        # explicito, NUNCA se reusa "antes" (se leeria como "no cambia nada",
        # engañoso: la cartera si cambia de tamaño, solo que no se sabe cuanto
        # cambia el riesgo).
        riesgo_despues = {"vol_anual_pct": None, "contribucion_pct": {}, "diversificacion_efectiva": None}
    else:
        riesgo_despues = riesgo_cartera(r.retornos, pesos_despues)

    lectura = []
    if confianza == "insuficiente":
        lectura.append("Historial insuficiente: todavía no se puede calcular correlación ni riesgo para este candidato.")
    else:
        nuevos = sorted({t for p in posiciones_despues for t in p["topes_nuevos"]})
        for p in posiciones_despues:
            if p["topes_nuevos"]:
                lectura.append(f"{p['ticker']}: rompe el tope de {', '.join(p['topes_nuevos'])}.")
        if redundante_con:
            lectura.append(f"Redundante (correlación ≥ {UMBRAL_REDUNDANCIA}) con: {', '.join(c['ticker'] for c in redundante_con)}.")
        if not nuevos and not redundante_con and corr_media_ponderada is not None:
            lectura.append(f"Diversifica: correlación media ponderada de {corr_media_ponderada:.2f} contra la cartera actual.")
        if confianza == "baja":
            lectura.append(f"Confianza baja: menos de {MIN_MESES_ALTA_CONFIANZA} meses de historial en común.")

    return {
        "ticker": ticker,
        "cubeta": cubeta,
        "sector": sector,
        "monto_usd": round(monto_usd, 2),
        "fuente_fondos": fuente_fondos,
        # OJO: cargar_fundamentales_rv.py SIEMPRE escribe una fila, incluso
        # para un ticker que Yahoo no resuelve (todo NULL, con el motivo en
        # campos_no_verificados._todo -- es el registro de que se intento,
        # "regla de oro" de no inventar nada). "La fila existe" no es lo
        # mismo que "hay datos utiles": `tipo_instrumento` solo se completa
        # si Yahoo resolvio Y el cross-check de nombre paso (mismo criterio
        # que usa screener_rv.py para decidir si mostrar las compuertas).
        "tiene_fundamentales": bool(fundamentales and fundamentales.get("tipo_instrumento") is not None),
        "totales": {
            "total_rv_antes_usd": round(total_rv_antes, 2),
            "total_rv_despues_usd": round(total_rv_despues, 2),
            "patrimonio_antes_usd": round(patrimonio_antes, 2),
            "patrimonio_despues_usd": round(patrimonio_despues, 2),
        },
        "posiciones": posiciones_despues,
        "cubetas": cubetas,
        "sectores": sectores,
        "correlacion": {
            "candidato_vs_posiciones": corr_candidato,
            "redundante_con": redundante_con,
            "umbral_redundancia": UMBRAL_REDUNDANCIA,
            "media_ponderada_vs_rv_actual": corr_media_ponderada,
        },
        "riesgo": {
            "antes": {"vol_anual_pct": riesgo_antes["vol_anual_pct"], "diversificacion_efectiva": riesgo_antes["diversificacion_efectiva"]},
            "despues": {"vol_anual_pct": riesgo_despues["vol_anual_pct"], "diversificacion_efectiva": riesgo_despues["diversificacion_efectiva"]},
            "contribucion_candidato_pct": riesgo_despues["contribucion_pct"].get(ticker) if ticker not in r.excluidos else None,
        },
        "confianza": confianza,
        "cobertura_candidato": r.cobertura.get(ticker),
        "frontera": {
            "disponible": False,
            "motivo": "El método de frontera eficiente (shrinkage Ledoit-Wolf) es el contenido de la Fase 5, todavía no está implementado.",
        },
        "lectura": lectura,
    }


def guardar_simulacion(con, ticker: str, monto_usd: float, fuente_fondos: str, resultado: dict) -> int:
    """Guardado EXPLICITO (v2.34) -- nunca se llama solo desde `simular()`."""
    cur = con.execute(
        "INSERT INTO simulaciones_compra_rv (ticker, monto_usd, fuente_fondos, resultado_json) VALUES (?, ?, ?, ?);",
        (ticker, monto_usd, fuente_fondos, json.dumps(resultado, ensure_ascii=False)),
    )
    con.commit()
    return cur.lastrowid
