"""
PWOS -- Tenencias, cubetas y concentracion de Renta Variable (rediseño
de la solapa "Analisis - Acciones", Fase 1, 2026-09-21).

Es un tablero de decision, no de recomendacion: muestra pesos, topes y
bandas contra los parametros de `parametros_rv`, nunca dice comprar o
vender.

Reusa `calcular_ganancia_por_activo` (dashboard.py) y el mismo filtro de
posiciones que rebalanceo_rv.py -- el total de RV tiene que coincidir con
el del rebalanceo, no se reimplementa la valorizacion. El patrimonio
bruto (efectivo + inversiones + inmuebles, SIN restar pasivos) lo recibe
el caller, calculado a partir de /api/resumen (patrimonio_total +
total_pasivos) -- este modulo no lo recalcula.

Sin dato = None, nunca un valor inventado: la cubeta sin asignar, el
sector sin clasificar (llega con fundamentales_rv en la Fase 3), el
semaforo y el estado de valor (Fases 2 y 3) salen None y la UI muestra
"Sin asignar"/"—".
"""
from datetime import date

from alertas_rv import calcular_alertas, REVISION_OBLIGATORIA_CAIDA_PCT
from dashboard import calcular_ganancia_por_activo
from graham_rv import leer_fundamentales, leer_tesis_valuacion, valuacion
from indicadores_rv import indicadores_activos, semaforo_de
from parametros_rv import leer_parametros
from rebalanceo_rv import TIPOS_RENTA_VARIABLE

SIN_CLASIFICAR = "Sin clasificar"

# (clave en tesis_inversion.cubeta, nombre para la UI, param min, param max)
CUBETAS = [
    ("valor_puro", "Valor Graham puro", "objetivo_cubeta_valor_min", "objetivo_cubeta_valor_max"),
    ("calidad", "Calidad a precio razonable", "objetivo_cubeta_calidad_min", "objetivo_cubeta_calidad_max"),
    ("mayor_riesgo", "Mayor riesgo", None, "objetivo_cubeta_riesgo_max"),
]

# Campos de texto de la tesis: si al menos uno tiene contenido, la
# posicion "tiene tesis". Asignar solo una cubeta o un peso objetivo NO
# cuenta como tesis (sin tesis no hay semaforo ni alertas).
CAMPOS_TEXTO_TESIS = (
    "motivo", "riesgo", "criterio_venta", "tendencia_secular",
    "ventaja_competitiva", "pre_mortem",
)


def _tesis_por_ticker(con) -> dict[str, dict]:
    cols = ("cubeta", "peso_objetivo_pct") + CAMPOS_TEXTO_TESIS
    filas = con.execute(
        f"SELECT a.ticker, {', '.join('t.' + c for c in cols)} "
        "FROM tesis_inversion t JOIN activos a ON a.id = t.activo_id;"
    ).fetchall()
    out = {}
    for ticker, *valores in filas:
        d = dict(zip(cols, valores))
        d["tiene_tesis"] = any((d[c] or "").strip() for c in CAMPOS_TEXTO_TESIS)
        out[ticker] = d
    return out


def _fecha_datos(con) -> str | None:
    fila = con.execute(
        """
        SELECT MAX(p.fecha) FROM precios p
        JOIN activos a ON a.id = p.activo_id
        WHERE a.tipo IN (?, ?);
        """,
        TIPOS_RENTA_VARIABLE,
    ).fetchone()
    return fila[0] if fila else None


def tenencias_rv(con, mep: float, ccl: float, patrimonio_bruto_usd: float, hoy: date | None = None) -> dict:
    """`hoy` solo para pruebas (fechas de revision/bitacora); en uso real es date.today()."""
    params = leer_parametros(con)
    tope_rv = params["tope_posicion_rv_pct"]
    tope_pat = params["tope_posicion_patrimonio_pct"]

    indicadores_por_ticker: dict[str, list[dict]] = {}
    for ind in indicadores_activos(con):
        indicadores_por_ticker.setdefault(ind["ticker"], []).append(ind)

    ganancia = calcular_ganancia_por_activo(con, mep, ccl)
    abiertas = [
        p for p in ganancia
        if p["tipo"] in TIPOS_RENTA_VARIABLE and p["posicion_abierta"]
    ]
    valuadas = [p for p in abiertas if p["valor_actual_usd"] is not None]
    sin_valuar = sorted(p["ticker"] for p in abiertas if p["valor_actual_usd"] is None)

    total_rv = sum(p["valor_actual_usd"] for p in valuadas)
    tesis = _tesis_por_ticker(con)
    fundamentales = leer_fundamentales(con)
    tesis_valuacion = leer_tesis_valuacion(con)

    posiciones = []
    for p in valuadas:
        valor = p["valor_actual_usd"]
        pct_rv = valor / total_rv * 100 if total_rv else 0.0
        pct_pat = valor / patrimonio_bruto_usd * 100 if patrimonio_bruto_usd else None
        t = tesis.get(p["ticker"], {})
        sem = semaforo_de(indicadores_por_ticker.get(p["ticker"], []))
        fund = fundamentales.get(p["ticker"])
        val = valuacion(fund, tesis_valuacion.get(p["ticker"]), t.get("cubeta")) if fund else None
        posiciones.append({
            "ticker": p["ticker"],
            "tipo": p["tipo"],
            "cantidad": p["cantidad"],
            "valor_usd": round(valor, 2),
            "pct_rv": round(pct_rv, 2),
            "pct_patrimonio": round(pct_pat, 2) if pct_pat is not None else None,
            "ganancia_pct": p["ganancia_porcentaje"],  # desde el costo en USD; None = no verificable
            "cubeta": t.get("cubeta"),
            "peso_objetivo_pct": t.get("peso_objetivo_pct"),
            "tiene_tesis": t.get("tiene_tesis", False),
            "semaforo": sem["estado"],  # peor estado entre los indicadores activos con lectura; None = gris
            "indicadores_total": sem["total"],
            "indicadores_con_dato": sem["con_dato"],
            # None = sin fundamentales cargados; si no, el estado (incluye 'sin_verificar' y 'no_aplica')
            "estado_valor": val["estado"] if val else None,
            "margen_valor_pct": val["margen_actual_pct"] if val else None,
            "sector": fund.get("sector") if fund else None,   # fundamentales_rv (Yahoo + SECTOR_MANUAL)
            "supera_tope_rv": pct_rv > tope_rv,
            "supera_tope_patrimonio": pct_pat is not None and pct_pat > tope_pat,
        })
    posiciones.sort(key=lambda f: -f["valor_usd"])

    top5_pct = sum(f["pct_rv"] for f in posiciones[:5])
    n_cedears = sum(1 for f in posiciones if f["tipo"] == "cedear")
    con_tesis = sum(1 for f in posiciones if f["tiene_tesis"])
    alertas = calcular_alertas(
        con, posiciones,
        {"rv": tope_rv, "patrimonio": tope_pat, "sector": params["tope_sector_rv_pct"]}, hoy=hoy)

    return {
        "fecha_datos": _fecha_datos(con),
        "patrimonio_bruto_usd": round(patrimonio_bruto_usd, 2),
        "parametros": {
            "tope_posicion_rv_pct": tope_rv,
            "tope_posicion_patrimonio_pct": tope_pat,
            "revision_obligatoria_caida_pct": REVISION_OBLIGATORIA_CAIDA_PCT,
        },
        "alertas": alertas,
        "kpis": {
            "valor_total_usd": round(total_rv, 2),
            "n_posiciones": len(posiciones),
            "n_cedears": n_cedears,
            "n_acciones": len(posiciones) - n_cedears,
            "peso_patrimonio_pct": round(total_rv / patrimonio_bruto_usd * 100, 2) if patrimonio_bruto_usd else None,
            "objetivo_rv_patrimonio_pct": params["peso_objetivo_rv_patrimonio_pct"],
            "concentracion_top5_pct": round(top5_pct, 2),
            "con_tesis": con_tesis,
            # Sale de la MISMA lista que el panel de alertas (solo las
            # 'activa'; las revisadas/justificadas siguen visibles pero no cuentan).
            "alertas_activas": sum(1 for a in alertas if a["estado"] == "activa"),
            "alertas_atenuadas": sum(1 for a in alertas if a["estado"] != "activa"),
        },
        "posiciones": posiciones,
        "sin_valuar": sin_valuar,
    }


CAMPOS_TESIS_FICHA = (
    "motivo", "riesgo", "horizonte", "precio_objetivo", "criterio_venta", "peso_objetivo_pct",
    "cubeta", "valor_intrinseco_min", "valor_intrinseco_max", "margen_seguridad_req_pct",
    "proxima_revision", "tendencia_secular", "ventaja_competitiva", "pre_mortem",
    "alertas_sumar", "alertas_reducir",
)


def obtener_tesis_rv(con, ticker: str) -> dict:
    """La tesis completa de un ticker, para precargar la Ficha de posicion.
    LookupError si el ticker no existe en `activos`; `existe`=False (y
    todos los campos None) si todavia no tiene fila de tesis."""
    activo = con.execute("SELECT id FROM activos WHERE ticker = ?;", (ticker,)).fetchone()
    if activo is None:
        raise LookupError(f"Ticker {ticker!r} no existe en 'activos'")
    fila = con.execute(
        f"SELECT {', '.join(CAMPOS_TESIS_FICHA)}, actualizado_en FROM tesis_inversion WHERE activo_id = ?;",
        (activo[0],),
    ).fetchone()
    if fila is None:
        return {"ticker": ticker, "existe": False, **{c: None for c in CAMPOS_TESIS_FICHA}, "actualizado_en": None}
    return {"ticker": ticker, "existe": True, **dict(zip(CAMPOS_TESIS_FICHA + ("actualizado_en",), fila))}


def composicion_rv(con, tenencias: dict) -> dict:
    """Cubetas vs. banda objetivo, top 5 y concentracion por sector, a
    partir de lo que ya devolvio tenencias_rv (no vuelve a valorizar).
    Todos los porcentajes son sobre el TOTAL de RV, incluyendo lo sin
    asignar/sin clasificar -- asi las barras no se distorsionan cuando
    la carga esta incompleta."""
    params = leer_parametros(con)
    posiciones = tenencias["posiciones"]
    total = sum(f["valor_usd"] for f in posiciones)

    def pct_de(valor_usd: float) -> float:
        # sobre valores en USD, no sumando pct_rv ya redondeados (drift de 0,01)
        return valor_usd / total * 100 if total else 0.0

    cubetas = []
    for clave, nombre, p_min, p_max in CUBETAS:
        miembros = [f for f in posiciones if f["cubeta"] == clave]
        cubetas.append({
            "cubeta": clave,
            "nombre": nombre,
            "objetivo_min_pct": params[p_min] if p_min else None,
            "objetivo_max_pct": params[p_max],
            "actual_pct": round(pct_de(sum(f["valor_usd"] for f in miembros)), 2),
            "n_posiciones": len(miembros),
            "cargada": bool(miembros),
        })
    sin_asignar = [f for f in posiciones if f["cubeta"] is None]

    tope_sector = params["tope_sector_rv_pct"]
    por_sector: dict[str, float] = {}
    for f in posiciones:
        clave = f["sector"] or SIN_CLASIFICAR
        por_sector[clave] = por_sector.get(clave, 0.0) + pct_de(f["valor_usd"])
    sectores = sorted(
        (
            {
                "sector": s,
                "pct_rv": round(v, 2),
                "supera_tope": s != SIN_CLASIFICAR and v > tope_sector,
            }
            for s, v in por_sector.items()
        ),
        key=lambda x: (x["sector"] == SIN_CLASIFICAR, -x["pct_rv"]),
    )

    return {
        "cubetas": cubetas,
        "sin_asignar_pct": round(pct_de(sum(f["valor_usd"] for f in sin_asignar)), 2),
        "sin_asignar_n": len(sin_asignar),
        "top5": [
            {"ticker": f["ticker"], "pct_rv": f["pct_rv"], "supera_tope": f["supera_tope_rv"]}
            for f in posiciones[:5]
        ],
        "tope_posicion_rv_pct": params["tope_posicion_rv_pct"],
        "sectores": sectores,
        "tope_sector_rv_pct": tope_sector,
        "sin_clasificar_pct": round(por_sector.get(SIN_CLASIFICAR, 0.0), 2),
    }
