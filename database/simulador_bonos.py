"""
PWOS -- motor de analisis/simulacion de bonos (Epica 11).

Objetivo: que el usuario pueda mirar datos de bonos y correr simulaciones
("si compro a este precio, que TIR me da"; "si la TIR comprime a X%, a
que precio va") DENTRO del dashboard, sin entrar a docta.com.ar ni a la
app de un broker.

Reusa lo que ya existe, NO lo reimplementa:
  - `tir_bonos.xirr` -> motor de TIR por biseccion (mismo que usa la
    Herramienta 1 de rotacion). Precio y flujos SIEMPRE en la misma
    moneda -> la TIR/duration que sale es agnostica de moneda, igual que
    la que publica Rava.
  - `cronograma_pagos_bonos` (Rava, cargado por
    cargar_bonos_referencia_flujo.py) para bonos EN cartera.
  - `rava_client.obtener_flujo_bono(ticker)` en vivo para bonos que NO
    estan en cartera (el simulador tiene que servir para cualquier
    especie, no solo las 21 propias).
  - `mercado_renta_fija_rava` (snapshot de ~890 especies) para el
    `valor_tecnico` (necesario para paridad) y el precio de referencia.

Convencion de moneda del simulador: TODO se expresa "por VN 100, en la
moneda de PAGO del bono" (USD para un bono duro, ARS para una LECAP).
El que llama pasa el precio en esa moneda. `paridad`, `current_yield`,
etc. se calculan aca a partir del cronograma -- NO se leen de los
campos homonimos de Rava, que en el endpoint masivo vienen en 0/NULL
para la mayoria de los bonos (verificado 2026-09-08, ver
cargar_mercado_renta_fija_rava.py).

`valor_tecnico` de Rava (unico dato que si se toma de la tabla) esta
"por VN 100" y en la misma convencion de moneda del cronograma para el
caso normal -- para bonos duros Rava lo publica en USD. Si falta, la
paridad sale None en vez de un numero inventado.

Convenciones de calculo (validadas 2026-09-08 contra la duration/dm/
convexity que publica Rava para los 21 bonos de cartera):
  - Macaulay duration: coincide con `duration` de Rava dentro de <2%
    (la diferencia residual es day-count -- aca actual/365, Rava
    aparenta usar otra base). Suficiente para una herramienta de
    decision de rotacion, NO para pricing fino.
  - Modified duration: `D_mac / (1 + TIR/2)` -- convencion SEMESTRAL,
    que es la que usa Rava para los bonos con cupon (confirmado: el
    divisor implicito de Rava es (1 + TIR/2) en los Globales/Bonares).
  - Convexidad: coincide con Rava con 3-4 cifras; ademas se rellena
    para los ~4 tickers donde Rava la deja en NULL.
  - current_yield y paridad se calculan aca (Rava los da en 0 en el
    endpoint masivo).
"""

from datetime import date

from tir_bonos import xirr, TIPOS_RENTA_FIJA
from valorizacion import precio_actual, cotizaciones
import configuracion

DIAS_ANIO = 365.0


def _valor_presente(flujos_futuros: list[dict], tasa: float, fecha_ref: date) -> float:
    """VP (= precio teorico por VN 100) de una lista de flujos a `tasa`
    anual efectiva. `flujos_futuros`: [{fecha: date, total: float}, ...]."""
    return sum(
        f["total"] / (1 + tasa) ** ((f["fecha"] - fecha_ref).days / DIAS_ANIO)
        for f in flujos_futuros
    )


def flujos_futuros(con, ticker: str, fecha_ref: date) -> dict:
    """Cronograma que queda DESPUES de `fecha_ref`, por VN 100, en la
    moneda de pago del bono. Devuelve:
        {
          "fuente": "cartera" | "rava_vivo",
          "flujos": [{fecha, cupon, amortizacion, total}, ...] (ordenado),
          "moneda_pago": "USD"|"ARS"|"USD_LINKED"|None,
          "en_cartera": bool,
        }
    o None si no hay cobertura en ninguna de las 2 fuentes.
    """
    fila_activo = con.execute(
        "SELECT id FROM activos WHERE ticker = ?;", (ticker,)
    ).fetchone()
    activo_id = fila_activo[0] if fila_activo else None

    # 1. Bono en cartera con cronograma ya cargado -> fuente local.
    if activo_id is not None:
        crono = con.execute(
            """
            SELECT fecha_pago, tipo_pago, monto_por_vn
            FROM cronograma_pagos_bonos
            WHERE activo_id = ? AND fecha_pago > ?
            ORDER BY fecha_pago;
            """,
            (activo_id, fecha_ref.isoformat()),
        ).fetchall()
        if crono:
            moneda_pago = con.execute(
                "SELECT moneda_pago FROM bonos_referencia WHERE activo_id = ?;", (activo_id,)
            ).fetchone()
            por_fecha: dict[str, dict] = {}
            for fecha_pago, tipo_pago, monto_por_vn in crono:
                d = por_fecha.setdefault(
                    fecha_pago, {"fecha": date.fromisoformat(fecha_pago), "cupon": 0.0, "amortizacion": 0.0}
                )
                if tipo_pago == "interes":
                    d["cupon"] += monto_por_vn
                else:
                    d["amortizacion"] += monto_por_vn
            flujos = sorted(por_fecha.values(), key=lambda x: x["fecha"])
            for f in flujos:
                f["total"] = f["cupon"] + f["amortizacion"]
            return {
                "fuente": "cartera",
                "flujos": flujos,
                "moneda_pago": moneda_pago[0] if moneda_pago else None,
                "en_cartera": True,
            }

    # 2. Cualquier otra especie -> Rava en vivo.
    from rava_client import obtener_flujo_bono
    crudo = obtener_flujo_bono(ticker)
    if crudo is None:
        return None
    flujos = []
    for ev in crudo["flujos"]:
        f = date.fromisoformat(ev["fecha_pago"][:10])
        if f <= fecha_ref:
            continue
        cupon = float(ev["cupon"])
        amort = float(ev["principal"])
        flujos.append({"fecha": f, "cupon": cupon, "amortizacion": amort, "total": cupon + amort})
    if not flujos:
        return None
    flujos.sort(key=lambda x: x["fecha"])
    return {
        "fuente": "rava_vivo",
        "flujos": flujos,
        # esDolar de Rava NO es confiable para clasificar moneda_pago
        # (documentado en cargar_bonos_referencia_flujo.py) -> None,
        # el que llama decide.
        "moneda_pago": "USD" if crudo.get("esDolar") else None,
        "en_cartera": activo_id is not None,
    }


def metricas(precio_vn100: float, flujos: list[dict], fecha_ref: date,
             valor_tecnico: float | None = None) -> dict:
    """Metricas analiticas de un bono a un `precio_vn100` dado (compra
    hoy = `fecha_ref`). `flujos`: salida de flujos_futuros()["flujos"].

    TIR = tasa anual efectiva (xirr). TNA/TEM derivadas de la TIR con la
    convencion local (TEM = (1+TIR)^(1/12)-1, TNA = TEM*12). Duration de
    Macaulay y modificada + convexidad calculadas A la TIR. current
    yield = cupon de los proximos 365 dias / precio. paridad = precio /
    valor_tecnico (None si no hay valor_tecnico).
    """
    if precio_vn100 <= 0 or not flujos:
        return {"error": "precio <= 0 o sin flujos futuros"}

    flujos_para_tir = [(fecha_ref, -precio_vn100)] + [(f["fecha"], f["total"]) for f in flujos]
    tir = xirr(flujos_para_tir)

    venc = flujos[-1]["fecha"]
    dias_venc = (venc - fecha_ref).days
    anios_venc = dias_venc / DIAS_ANIO

    cupon_12m = sum(
        f["cupon"] for f in flujos if 0 < (f["fecha"] - fecha_ref).days <= 365
    )
    current_yield = cupon_12m / precio_vn100 if precio_vn100 else None

    resultado = {
        "precio_vn100": round(precio_vn100, 4),
        "tir_pct": round(tir * 100, 4) if tir is not None else None,
        "tna_pct": None,
        "tem_pct": None,
        "duration_macaulay": None,
        "duration_modificada": None,
        "convexidad": None,
        "current_yield_pct": round(current_yield * 100, 4) if current_yield is not None else None,
        "dias_al_vencimiento": dias_venc,
        "anios_al_vencimiento": round(anios_venc, 4),
        "fecha_vencimiento": venc.isoformat(),
        "paridad_pct": round(precio_vn100 / valor_tecnico * 100, 4) if valor_tecnico else None,
        "valor_tecnico": round(valor_tecnico, 4) if valor_tecnico else None,
        "total_a_cobrar_vn100": round(sum(f["total"] for f in flujos), 4),
        "interes_futuro_vn100": round(sum(f["cupon"] for f in flujos), 4),
        "amortizacion_futura_vn100": round(sum(f["amortizacion"] for f in flujos), 4),
        "n_pagos_futuros": len(flujos),
    }

    if tir is None:
        resultado["motivo"] = "xirr no convergio (sin raiz en [-99%, 1000%])"
        return resultado

    tem = (1 + tir) ** (1 / 12) - 1
    resultado["tem_pct"] = round(tem * 100, 4)
    resultado["tna_pct"] = round(tem * 12 * 100, 4)

    # Duration / convexidad a la TIR. suma_pv deberia dar ~= precio_vn100
    # (chequeo de consistencia implicito).
    suma_pv = suma_t_pv = suma_t2_pv = 0.0
    for f in flujos:
        t = (f["fecha"] - fecha_ref).days / DIAS_ANIO
        pv = f["total"] / (1 + tir) ** t
        suma_pv += pv
        suma_t_pv += t * pv
        suma_t2_pv += t * (t + 1) * pv

    if suma_pv > 0:
        d_mac = suma_t_pv / suma_pv
        resultado["duration_macaulay"] = round(d_mac, 4)
        # Convencion semestral (la que usa Rava para bonos con cupon) --
        # ver docstring del modulo.
        resultado["duration_modificada"] = round(d_mac / (1 + tir / 2), 4)
        resultado["convexidad"] = round(suma_t2_pv / (suma_pv * (1 + tir) ** 2), 4)

    return resultado


def precio_desde_tir(flujos: list[dict], tir_objetivo_pct: float, fecha_ref: date) -> float:
    """Precio por VN 100 que hace que la TIR sea exactamente
    `tir_objetivo_pct` (%). Es simplemente el valor presente de los
    flujos a esa tasa."""
    return _valor_presente(flujos, tir_objetivo_pct / 100.0, fecha_ref)


def _datos_mercado_rava(con, ticker: str) -> dict | None:
    """Foto de hoy publicada por Rava (mercado_renta_fija_rava). tir/
    duration/dm/convexity los calcula Rava a partir del precio real de
    mercado -> se toman tal cual (matchean docta por construccion). El
    `precio` crudo NO se usa para calcular nada (endpoint masivo sin
    normalizar moneda/escala) -- se devuelve solo como referencia visual."""
    fila = con.execute(
        """
        SELECT nombre, tipo_instrumento, legislacion, tir, duration, dm,
               convexity, valor_tecnico, precio, actualizado_en
        FROM mercado_renta_fija_rava WHERE ticker = ?;
        """,
        (ticker,),
    ).fetchone()
    if fila is None:
        return None
    nombre, tipo_inst, ley, tir, dur, dm, cvx, vt, precio_crudo, act = fila
    _r = lambda x, n=4: round(x, n) if x is not None else None
    return {
        "nombre": nombre, "tipo_instrumento": tipo_inst, "legislacion": ley,
        "tir_pct": _r(tir * 100 if tir is not None else None),
        "duration": _r(dur), "dm": _r(dm), "convexidad": _r(cvx, 2),
        "valor_tecnico": _r(vt), "precio_crudo_rava": precio_crudo,
        "actualizado_en": act[:10] if act else None,
    }


def _posicion_pwos(con, ticker: str, moneda_pago: str | None, mep: float | None) -> dict | None:
    """Posicion vigente del usuario + su precio actual (PWOS `precios`),
    por VN 100 en la moneda de pago del bono. None si no hay tenencia
    vigente. Bonos duros guardados en ARS (especie O) se convierten al
    MEP -- mismo criterio que tir_bonos.py."""
    fila = con.execute(
        "SELECT SUM(cantidad) FROM vista_posiciones vp JOIN activos a ON a.id = vp.activo_id WHERE a.ticker = ?;",
        (ticker,),
    ).fetchone()
    cantidad = fila[0] if fila else None
    if not cantidad or abs(cantidad) < 1e-6:
        return None

    prc = con.execute(
        """
        SELECT vpa.activo_id, vpa.fecha
        FROM vista_precio_actual vpa JOIN activos a ON a.id = vpa.activo_id
        WHERE a.ticker = ?;
        """,
        (ticker,),
    ).fetchone()

    res = {"cantidad": cantidad, "precio_vn100": None, "origen_precio": None}
    actual = precio_actual(con, prc[0]) if prc else None  # en vivo en la rueda (ADR-033)
    if actual and actual[0]:
        precio, moneda = actual
        est = cotizaciones(con)
        fecha = f"en vivo {est['hora']}" if est and est["modo"] == "vivo" and prc[0] in est["precios"] else prc[1]
        if moneda_pago is None or moneda == moneda_pago:
            res["precio_vn100"] = precio
            res["origen_precio"] = f"precios PWOS ({fecha})"
        elif moneda == "ARS" and moneda_pago == "USD" and mep:
            res["precio_vn100"] = precio / mep
            res["origen_precio"] = f"precios PWOS ({fecha}), ARS->USD al MEP {mep:.2f}"
    return res


def simular(con, ticker: str, precio_vn100: float | None = None,
            tir_objetivo_pct: float | None = None, fecha_ref: date | None = None,
            mep: float | None = None) -> dict:
    """Analisis + simulacion de un bono.

    Siempre devuelve el bloque `mercado` (foto de hoy segun Rava, con un
    precio LIMPIO derivado de la TIR que publica Rava -- nunca el
    `precio` crudo del endpoint masivo). Si el bono esta en cartera,
    tambien `posicion` (tu tenencia + tu precio PWOS). Si se pasa
    `precio_vn100` o `tir_objetivo_pct`, ademas `simulacion`.

    `mep`: para convertir a USD el precio PWOS de un bono duro guardado
    en ARS. api.py lo pasa con obtener_precio_fx(con, "USD_MEP").
    """
    ticker = ticker.strip().upper()
    fecha_ref = fecha_ref or configuracion.hoy()

    ff = flujos_futuros(con, ticker, fecha_ref)
    if ff is None:
        return {"ticker": ticker, "error": "sin cobertura de cronograma en cartera ni en Rava"}

    flujos = ff["flujos"]
    moneda_pago = ff["moneda_pago"]
    en_cartera = _posicion_pwos(con, ticker, moneda_pago, mep) is not None
    mkt = _datos_mercado_rava(con, ticker)
    valor_tecnico = (mkt or {}).get("valor_tecnico")

    venc = flujos[-1]["fecha"]
    salida: dict = {
        "ticker": ticker,
        "nombre": (mkt or {}).get("nombre"),
        "tipo_instrumento": (mkt or {}).get("tipo_instrumento"),
        "legislacion": (mkt or {}).get("legislacion"),
        "fecha_ref": fecha_ref.isoformat(),
        "fuente_cronograma": ff["fuente"],
        "en_cartera": en_cartera,
        "moneda_pago": moneda_pago,
        "fecha_vencimiento": venc.isoformat(),
        "dias_al_vencimiento": (venc - fecha_ref).days,
        "anios_al_vencimiento": round((venc - fecha_ref).days / DIAS_ANIO, 4),
        "n_pagos_futuros": len(flujos),
        "total_a_cobrar_vn100": round(sum(f["total"] for f in flujos), 4),
        "interes_futuro_vn100": round(sum(f["cupon"] for f in flujos), 4),
        "amortizacion_futura_vn100": round(sum(f["amortizacion"] for f in flujos), 4),
        "valor_tecnico": round(valor_tecnico, 4) if valor_tecnico else None,
        "cronograma": [
            {"fecha": f["fecha"].isoformat(), "cupon": round(f["cupon"], 6),
             "amortizacion": round(f["amortizacion"], 6), "total": round(f["total"], 6)}
            for f in flujos
        ],
    }

    # --- Bloque MERCADO: precio limpio = el que implica la TIR de Rava.
    precio_mercado = None
    if mkt and mkt["tir_pct"] is not None:
        precio_mercado = precio_desde_tir(flujos, mkt["tir_pct"], fecha_ref)
        m = metricas(precio_mercado, flujos, fecha_ref, valor_tecnico)
        salida["mercado"] = {
            "actualizado_en": mkt["actualizado_en"],
            "precio_implicito_vn100": round(precio_mercado, 4),
            "precio_crudo_rava": mkt["precio_crudo_rava"],
            "tir_pct": mkt["tir_pct"],           # publicada por Rava
            "tna_pct": m["tna_pct"],
            "tem_pct": m["tem_pct"],
            "duration_rava": mkt["duration"],    # publicada por Rava
            "duration_calculada": m["duration_macaulay"],
            "duration_modificada": mkt["dm"] if mkt["dm"] is not None else m["duration_modificada"],
            "convexidad": mkt["convexidad"] if mkt["convexidad"] is not None else m["convexidad"],
            "current_yield_pct": m["current_yield_pct"],
            "paridad_pct": m["paridad_pct"],
        }
    else:
        salida["mercado"] = {"motivo": "sin fila en mercado_renta_fija_rava (bono nuevo / sin cobertura Rava)"}

    # --- Bloque POSICION: tu tenencia + tu precio.
    pos = _posicion_pwos(con, ticker, moneda_pago, mep)
    if pos:
        bloque = {"cantidad": pos["cantidad"], "precio_vn100": pos["precio_vn100"],
                  "origen_precio": pos["origen_precio"]}
        if pos["precio_vn100"]:
            bloque["metricas"] = metricas(pos["precio_vn100"], flujos, fecha_ref, valor_tecnico)
        salida["posicion"] = bloque

    # --- Bloque SIMULACION.
    if precio_vn100 is not None or tir_objetivo_pct is not None:
        if tir_objetivo_pct is not None:
            precio_sim = precio_desde_tir(flujos, tir_objetivo_pct, fecha_ref)
            entrada = {"tir_objetivo_pct": tir_objetivo_pct}
        else:
            precio_sim = precio_vn100
            entrada = {"precio_vn100": precio_vn100}

        sim = {
            "input": entrada,
            "precio_vn100": round(precio_sim, 4),
            "metricas": metricas(precio_sim, flujos, fecha_ref, valor_tecnico),
        }
        if precio_mercado:
            sim["vs_mercado"] = {
                "precio_mercado_vn100": round(precio_mercado, 4),
                "delta_precio_pct": round((precio_sim / precio_mercado - 1) * 100, 4),
                "nota": "Delta de precio vs. el precio que implica la TIR de Rava de hoy (variacion pura, sin contar el cupon del camino).",
            }
        salida["simulacion"] = sim

    return salida


def _es_renta_fija(tipo: str) -> bool:
    return tipo in TIPOS_RENTA_FIJA


if __name__ == "__main__":
    import sqlite3
    from pathlib import Path

    db = configuracion.ruta_db()
    con = sqlite3.connect(db)

    mep = con.execute(
        "SELECT precio FROM vista_precio_actual vpa JOIN activos a ON a.id = vpa.activo_id WHERE a.ticker = 'USD_MEP';"
    ).fetchone()[0]

    # Prueba manual: bono en cartera (con simulacion) + bono fuera de cartera.
    import json
    for tk, precio, tir_obj in [("GD30", None, 8.0), ("AL30", None, None), ("GD35", 78.0, None)]:
        print("=" * 70)
        print(f"simular({tk!r}, precio={precio}, tir_objetivo={tir_obj})  MEP={mep}")
        print("=" * 70)
        r = simular(con, tk, precio_vn100=precio, tir_objetivo_pct=tir_obj, mep=mep)
        r.pop("cronograma", None)  # muy largo para el print
        print(json.dumps(r, indent=2, ensure_ascii=False))
        print()
    con.close()
