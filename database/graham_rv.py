"""
PWOS -- Reglas Graham estricto / Graham-Plus, compuertas del screener y
estado de valor (Epica 19, Fase 3, 2026-09-21). Modulo PURO: recibe la fila
de `fundamentales_rv` ya leida, no toca la red ni depende de las tenencias.

Es un tablero de decision, no de recomendacion: cada criterio muestra el dato,
el umbral y su estado; nunca dice comprar o vender.

REGLA DE ORO: un dato que falta NUNCA hace "pasar" un
criterio. Estados de un criterio:
  pasa | no_pasa | sin_verificar | no_aplica
Un criterio `no_aplica` no cuenta (ej. P/B en un negocio liviano). El veredicto
de un grupo de criterios que BLOQUEAN es:
  no_pasa       si alguno verificado falla (no hace falta esperar al resto)
  sin_verificar si ninguno falla pero alguno no se pudo verificar
  pasa          solo si TODOS los que bloquean pasan
Consecuencia esperada y buscada: Yahoo da 4-5 años de estados y ningun P/E
historico, asi que las reglas de 10 años (ganancias, EPS creciente, P/E vs su
promedio) y la deuda de largo plazo / activos corrientes netos NO son
verificables: "Graham estricto" solo puede dar no_pasa o sin_verificar, y la
compuerta de precio de Calidad tampoco llega a "pasa". Una perdida en los
años disponibles SI se detecta (falla sin esperar al resto).

Umbrales: constantes de este modulo (definiciones del marco del usuario, versionadas
en git). Los marcados BORRADOR no estan definidos en su Apendice A y son
propuestas a calibrar.

Valor intrinseco (decision del usuario): el Numero de Graham sqrt(22,5 x EPS x BVPS)
solo se usa como VI en la cubeta VALOR PURO (en un negocio liviano daria siempre
"sobrevalorada"); en Calidad y Mayor riesgo el estado de valor exige el rango
cargado a mano (`tesis_inversion.valor_intrinseco_min/max`, en USD por accion del
subyacente). VI conservador = el menor de los disponibles. Margen de seguridad
requerido: el de la posicion si lo cargo; si no, el default de su cubeta
(BORRADOR: valor puro 30 %, calidad 20 %, mayor riesgo 40 %); sin cubeta ni
margen, sin verificar.
"""
import json
import math

# --- Graham estricto (Apendice A) -------------------------------------------
GRAHAM_PER_MAX = 15.0
GRAHAM_PB_MAX = 1.5
GRAHAM_PER_PB_MAX = 22.5
GRAHAM_CURRENT_RATIO_MIN = 2.0
GRAHAM_DIVIDENDO_ANIOS = 20
GRAHAM_ANIOS_GANANCIAS = 10

# --- Graham-Plus (Apendice A) ------------------------------------------------
PLUS_CURRENT_RATIO_MIN = 1.5
PLUS_PER_FORWARD_MAX = 25.0
PLUS_ND_EBITDA_MAX = 3.0           # objetivo <= 2
PLUS_ND_EBITDA_OBJETIVO = 2.0
PLUS_COBERTURA_MIN = 5.0
PLUS_ROIC_MIN = 12.0               # el Apendice dice 12-15 %: se exige 12, objetivo 15
PLUS_ROIC_OBJETIVO = 15.0
PLUS_FCF_YIELD_MIN = {"valor_puro": 4.0, "calidad": 3.0}   # el Apendice dice 4-5 % en valor puro
PLUS_DIVIDENDO_ANIOS = 10
PLUS_ANIOS_GANANCIAS = 5

# --- BORRADORES mios (no estan definidos en el Apendice A) --------------------
FCF_NEGATIVO_MIN_ANIOS = 3          # "FCF negativo sostenido" = todos los años con dato, y al menos 3
MARGEN_ESTABLE_RANGO_PP = 10.0      # "margenes estables": maximo - minimo del margen operativo <= 10 pp (>= 3 años)
CONVERSION_CAJA_MIN = 0.8           # "conversion a caja": FCF / utilidad neta promedio >= 0,8 (>= 3 años)
MIN_ANIOS_SERIE = 3
CAIDA_DIVIDENDO_SOSPECHOSA_PCT = -50  # un corte de racha mayor a esto se lee como posible ajuste de split de Yahoo
MARGEN_REQ_DEFAULT = {"valor_puro": 30.0, "calidad": 20.0, "mayor_riesgo": 40.0}
VALOR_JUSTO_HASTA_PCT = 110.0       # Apendice A: valor justo hasta el 110 % del valor intrinseco

CUBETAS = ("valor_puro", "calidad", "mayor_riesgo")

ETIQUETAS_ESTADO = {"pasa": "pasa", "no_pasa": "no pasa", "sin_verificar": "sin verificar", "no_aplica": "no aplica"}


def _fmt(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def _f4(v: float) -> str:
    return f"{v:,.4g}".replace(",", "")


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------

def leer_fundamentales(con) -> dict[str, dict]:
    """ticker -> fila de fundamentales_rv con `nv` (campos_no_verificados) y `series` ya parseados."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(fundamentales_rv);")]
    out = {}
    for fila in con.execute("SELECT * FROM fundamentales_rv;"):
        d = dict(zip(cols, fila))
        d["nv"] = json.loads(d["campos_no_verificados"]) if d.get("campos_no_verificados") else {}
        d["series"] = json.loads(d["series_json"]) if d.get("series_json") else {}
        out[d["ticker"]] = d
    return out


def leer_tesis_valuacion(con) -> dict[str, dict]:
    """ticker -> campos de tesis_inversion que usa la valuacion (cargados a mano)."""
    return {
        t: {"cubeta": c, "valor_intrinseco_min": vmin, "valor_intrinseco_max": vmax, "margen_seguridad_req_pct": m}
        for t, c, vmin, vmax, m in con.execute(
            """
            SELECT a.ticker, t.cubeta, t.valor_intrinseco_min, t.valor_intrinseco_max, t.margen_seguridad_req_pct
            FROM tesis_inversion t JOIN activos a ON a.id = t.activo_id;
            """
        )
    }


# ---------------------------------------------------------------------------
# Criterios
# ---------------------------------------------------------------------------

def _criterio(clave, nombre, umbral_txt, estado, valor=None, detalle=None, bloquea=True) -> dict:
    return {"clave": clave, "nombre": nombre, "umbral": umbral_txt, "valor": valor,
            "estado": estado, "detalle": detalle, "bloquea": bloquea}


def _motivo(f: dict, campo: str) -> dict | None:
    return f["nv"].get(campo)


def _num_crit(f, clave, nombre, campo, op, umbral, *, no_aplica_es_fallo=False, bloquea=True,
              detalle_ok=None) -> dict:
    """Criterio numerico contra un umbral, sobre la columna `campo`. Sin valor: el
    motivo de campos_no_verificados; 'sin_intereses' pasa (es un dato); 'no_aplica'
    (EPS <= 0, patrimonio <= 0) es un FALLO cuando `no_aplica_es_fallo`, si no queda
    sin verificar."""
    txt = f"{op} {_fmt(umbral)}"
    v = f.get(campo)
    if v is not None:
        ok = {"<": v < umbral, "<=": v <= umbral, ">": v > umbral, ">=": v >= umbral}[op]
        return _criterio(clave, nombre, txt, "pasa" if ok else "no_pasa", round(v, 4), detalle_ok, bloquea)
    m = _motivo(f, campo) or {}
    codigo, detalle = m.get("codigo"), m.get("detalle") or "sin dato"
    if codigo == "sin_intereses":
        return _criterio(clave, nombre, txt, "pasa", None, detalle, bloquea)
    if codigo == "no_aplica" and no_aplica_es_fallo:
        return _criterio(clave, nombre, txt, "no_pasa", None, detalle, bloquea)
    return _criterio(clave, nombre, txt, "sin_verificar", None, detalle, bloquea)


def _anios_utilidad(f) -> list[tuple[str, float]]:
    anual = (f["series"] or {}).get("anual") or {}
    return [(a, d["utilidad_neta"]) for a, d in sorted(anual.items()) if d.get("utilidad_neta") is not None]


def _crit_ganancias(f, clave, nombre, anios_objetivo, bloquea=True) -> dict:
    """Una perdida en los años disponibles falla sin esperar al resto; si no hay
    perdidas, NO se puede dar por pasado (Yahoo da 4-5 años, se piden 5 o 10)."""
    txt = f"sin pérdidas en {anios_objetivo} años"
    datos = _anios_utilidad(f)
    perdidas = [a for a, v in datos if v < 0]
    if perdidas:
        return _criterio(clave, nombre, txt, "no_pasa", None, f"pérdida en {', '.join(perdidas)}", bloquea)
    return _criterio(clave, nombre, txt, "sin_verificar", None,
                     f"solo hay {len(datos)} años de estados en Yahoo (se necesitan {anios_objetivo}); sin pérdidas en esos {len(datos)}"
                     if datos else "sin estados anuales", bloquea)


def _crit_dividendos(f, clave, nombre, minimo, bloquea=True) -> dict:
    txt = f"≥ {minimo} años sin recorte"
    n = f.get("anios_dividendo_sin_recorte")
    if n is None:
        return _criterio(clave, nombre, txt, "sin_verificar", None, "sin dato de dividendos", bloquea)
    if n >= minimo:
        return _criterio(clave, nombre, txt, "pasa", n, None, bloquea)
    corte = ((f["series"] or {}).get("racha_dividendos") or {}).get("cortada_en")
    if corte and corte.get("caida_pct") is not None and corte["caida_pct"] <= CAIDA_DIVIDENDO_SOSPECHOSA_PCT:
        return _criterio(clave, nombre, txt, "sin_verificar", n,
                         f"racha cortada en {corte['anio']} por una caída de {_fmt(corte['caida_pct'])} %: posible ajuste de split mal aplicado por Yahoo (la racha es un piso)",
                         bloquea)
    return _criterio(clave, nombre, txt, "no_pasa", n, None, bloquea)


def _crit_fcf_negativo(f) -> dict:
    txt = f"FCF no negativo sostenido (< 0 en todos los años, mín. {FCF_NEGATIVO_MIN_ANIOS})"
    anual = (f["series"] or {}).get("anual") or {}
    fcf = [d["fcf"] for d in anual.values() if d.get("fcf") is not None]
    if any(v > 0 for v in fcf):
        return _criterio("fcf_negativo", "FCF negativo sostenido", txt, "pasa", None, "hay años con FCF positivo")
    if len(fcf) >= FCF_NEGATIVO_MIN_ANIOS:
        return _criterio("fcf_negativo", "FCF negativo sostenido", txt, "no_pasa", None, f"FCF negativo en los {len(fcf)} años con dato")
    return _criterio("fcf_negativo", "FCF negativo sostenido", txt, "sin_verificar", None,
                     f"solo {len(fcf)} años de FCF (se necesitan {FCF_NEGATIVO_MIN_ANIOS} para concluir)" if fcf else "sin FCF en los estados")


def _crit_margenes(f) -> dict:
    txt = f"rango del margen operativo ≤ {_fmt(MARGEN_ESTABLE_RANGO_PP)} pp"
    anual = (f["series"] or {}).get("anual") or {}
    m = [d["margen_operativo"] for d in anual.values() if d.get("margen_operativo") is not None]
    if len(m) < MIN_ANIOS_SERIE:
        return _criterio("margenes", "Márgenes estables (borrador)", txt, "sin_verificar", None, f"solo {len(m)} años con margen (se necesitan {MIN_ANIOS_SERIE})")
    rango = max(m) - min(m)
    return _criterio("margenes", "Márgenes estables (borrador)", txt, "pasa" if rango <= MARGEN_ESTABLE_RANGO_PP else "no_pasa",
                     round(rango, 2), f"margen operativo entre {_fmt(round(min(m), 1))} % y {_fmt(round(max(m), 1))} %")


def _crit_conversion_caja(f) -> dict:
    txt = f"FCF / utilidad neta ≥ {_fmt(CONVERSION_CAJA_MIN)}"
    anual = (f["series"] or {}).get("anual") or {}
    razones = [d["fcf"] / d["utilidad_neta"] for d in anual.values()
               if d.get("fcf") is not None and d.get("utilidad_neta") and d["utilidad_neta"] > 0]
    if len(razones) < MIN_ANIOS_SERIE:
        return _criterio("conversion_caja", "Conversión a caja (borrador)", txt, "sin_verificar", None,
                         f"solo {len(razones)} años con utilidad positiva y FCF (se necesitan {MIN_ANIOS_SERIE})")
    prom = sum(razones) / len(razones)
    return _criterio("conversion_caja", "Conversión a caja (borrador)", txt, "pasa" if prom >= CONVERSION_CAJA_MIN else "no_pasa", round(prom, 3), None)


def veredicto(criterios: list[dict]) -> str:
    bloqueantes = [c for c in criterios if c["bloquea"] and c["estado"] != "no_aplica"]
    if any(c["estado"] == "no_pasa" for c in bloqueantes):
        return "no_pasa"
    if not bloqueantes or any(c["estado"] == "sin_verificar" for c in bloqueantes):
        return "sin_verificar"
    return "pasa"


def _crit_per_pb(f, umbral=GRAHAM_PER_PB_MAX) -> dict:
    """P/E x P/B < 22,5. Necesita AMBOS: si falta uno NUNCA se reutiliza el otro.
    Si alguno es no_aplica (EPS o patrimonio <= 0) el criterio falla: una empresa
    con perdidas o patrimonio negativo no cumple la regla de Graham."""
    clave, nombre, txt = "per_pb", "P/E × P/B", f"< {_fmt(umbral)}"
    per, pb = f.get("per"), f.get("pb")
    if per is not None and pb is not None:
        v = per * pb
        return _criterio(clave, nombre, txt, "pasa" if v < umbral else "no_pasa", round(v, 4))
    faltantes = [c for c in ("per", "pb") if f.get(c) is None]
    for campo in faltantes:
        m = _motivo(f, campo) or {}
        if m.get("codigo") == "no_aplica":
            return _criterio(clave, nombre, txt, "no_pasa", None, m.get("detalle"))
    m = _motivo(f, faltantes[0]) or {}
    return _criterio(clave, nombre, txt, "sin_verificar", None, m.get("detalle") or f"sin {faltantes[0].upper()} verificado")


# ---------------------------------------------------------------------------
# Compuertas C0, C1, C2 (C3 necesita las tenencias: screener_rv.py)
# ---------------------------------------------------------------------------

def compuerta_c0(f: dict) -> dict:
    """Descartes duros CUANTITATIVOS. 'Fuera del circulo de competencia' es un juicio
    del usuario, no un dato: no se evalua ni bloquea, va como recordatorio."""
    crit = [
        _num_crit(f, "nd_ebitda", "Deuda neta / EBITDA", "deuda_neta_ebitda", "<=", PLUS_ND_EBITDA_MAX),
        _num_crit(f, "cobertura", "Cobertura de intereses", "cobertura_intereses", ">=", PLUS_COBERTURA_MIN),
        _crit_fcf_negativo(f),
    ]
    return {"estado": veredicto(crit), "criterios": crit,
            "recordatorio": "Círculo de competencia: juicio tuyo, no se evalúa automáticamente."}


def compuerta_c1(f: dict) -> dict:
    crit = [
        _num_crit(f, "roic", "ROIC", "roic", ">=", PLUS_ROIC_MIN, detalle_ok=f"objetivo ≥ {_fmt(PLUS_ROIC_OBJETIVO)} %"),
        _crit_margenes(f),
        _crit_conversion_caja(f),
    ]
    return {"estado": veredicto(crit), "criterios": crit}


def criterios_precio(f: dict, cubeta: str | None) -> list[dict]:
    """Criterios de PRECIO de Graham-Plus (los de la compuerta C2). Sin cubeta no
    hay umbrales que aplicar."""
    if cubeta == "valor_puro":
        return [
            _num_crit(f, "per", "P/E", "per", "<", GRAHAM_PER_MAX, no_aplica_es_fallo=True),
            _num_crit(f, "pb", "P/B", "pb", "<", GRAHAM_PB_MAX, no_aplica_es_fallo=True),
            _crit_per_pb(f),
            _num_crit(f, "fcf_yield", "FCF yield (%)", "fcf_yield", ">=", PLUS_FCF_YIELD_MIN["valor_puro"]),
        ]
    if cubeta in ("calidad", "mayor_riesgo"):
        return [
            _criterio("per_hist", "P/E vs su promedio de 10 años", "≤ 110 % del promedio", "sin_verificar", None,
                      "Yahoo no da P/E histórico"),
            _num_crit(f, "per_forward", "P/E forward", "per_forward", "<=", PLUS_PER_FORWARD_MAX),
            _num_crit(f, "fcf_yield", "FCF yield (%)", "fcf_yield", ">=", PLUS_FCF_YIELD_MIN["calidad"]),
        ]
    return []


def compuerta_c2(f: dict, cubeta: str | None) -> dict:
    crit = criterios_precio(f, cubeta)
    if not crit:
        return {"estado": "sin_verificar", "criterios": [], "nota": "sin cubeta asignada: no hay umbrales de precio que aplicar"}
    out = {"estado": veredicto(crit), "criterios": crit}
    if cubeta == "mayor_riesgo":
        out["nota"] = "Mayor riesgo no tiene criterios propios en el marco: se aplican los de Calidad."
    return out


# ---------------------------------------------------------------------------
# Tablas de la ficha: Graham estricto vs Graham-Plus
# ---------------------------------------------------------------------------

def tabla_estricto(f: dict) -> list[dict]:
    return [
        _num_crit(f, "per", "P/E", "per", "<", GRAHAM_PER_MAX, no_aplica_es_fallo=True),
        _num_crit(f, "pb", "P/B", "pb", "<", GRAHAM_PB_MAX, no_aplica_es_fallo=True),
        _crit_per_pb(f),
        _num_crit(f, "current_ratio", "Current ratio", "current_ratio", ">", GRAHAM_CURRENT_RATIO_MIN),
        _criterio("deuda_lp", "Deuda LP / activos corrientes netos", "< 1", "sin_verificar", None,
                  "no se calcula: Yahoo no separa la deuda de largo plazo de forma confiable"),
        _crit_ganancias(f, "ganancias", "Ganancias positivas", GRAHAM_ANIOS_GANANCIAS),
        _crit_dividendos(f, "dividendos", "Dividendos", GRAHAM_DIVIDENDO_ANIOS),
    ]


def tabla_plus(f: dict, cubeta: str | None) -> tuple[list[dict], str | None]:
    """(criterios, nota). Sin cubeta: sin tabla (no hay umbrales que aplicar)."""
    if cubeta not in CUBETAS:
        return [], "Asigná una cubeta a la posición para ver los umbrales de Graham-Plus."
    valor_puro = cubeta == "valor_puro"
    t = []
    if valor_puro:
        t += [
            _num_crit(f, "per", "P/E", "per", "<", GRAHAM_PER_MAX, no_aplica_es_fallo=True),
            _num_crit(f, "pb", "P/B", "pb", "<", GRAHAM_PB_MAX, no_aplica_es_fallo=True),
            _crit_per_pb(f),
        ]
    else:
        t += [
            _criterio("per_hist", "P/E vs su promedio de 10 años", "≤ 110 % del promedio", "sin_verificar", None, "Yahoo no da P/E histórico"),
            _num_crit(f, "per_forward", "P/E forward", "per_forward", "<=", PLUS_PER_FORWARD_MAX),
            _criterio("pb", "P/B", "solo valor puro", "no_aplica", None, "en negocios livianos se reemplaza por ROIC y FCF yield", False),
            _criterio("per_pb", "P/E × P/B", "solo valor puro", "no_aplica", None, None, False),
        ]
    t += [
        _num_crit(f, "current_ratio", "Current ratio", "current_ratio", ">=", PLUS_CURRENT_RATIO_MIN,
                  detalle_ok="excepción documentable: capital de trabajo negativo por diseño"),
        _num_crit(f, "nd_ebitda", "Deuda neta / EBITDA", "deuda_neta_ebitda", "<=", PLUS_ND_EBITDA_MAX, detalle_ok=f"objetivo ≤ {_fmt(PLUS_ND_EBITDA_OBJETIVO)}"),
        _num_crit(f, "cobertura", "Cobertura de intereses", "cobertura_intereses", ">=", PLUS_COBERTURA_MIN),
        _crit_ganancias(f, "ganancias", "Ganancias y EPS creciente", PLUS_ANIOS_GANANCIAS),
        _crit_dividendos(f, "dividendos", "Dividendos", PLUS_DIVIDENDO_ANIOS, bloquea=valor_puro),
        _num_crit(f, "roic", "ROIC", "roic", ">=", PLUS_ROIC_MIN, detalle_ok=f"objetivo ≥ {_fmt(PLUS_ROIC_OBJETIVO)} %"),
        _num_crit(f, "fcf_yield", "FCF yield (%)", "fcf_yield", ">=", PLUS_FCF_YIELD_MIN["valor_puro" if valor_puro else "calidad"]),
    ]
    nota = "Mayor riesgo no tiene criterios propios en el marco: se aplican los de Calidad." if cubeta == "mayor_riesgo" else None
    return t, nota


# ---------------------------------------------------------------------------
# Valor intrinseco y estado de valor
# ---------------------------------------------------------------------------

def numero_de_graham(eps: float | None, bvps: float | None) -> float | None:
    if eps is not None and bvps is not None and eps > 0 and bvps > 0:
        return math.sqrt(22.5 * eps * bvps)
    return None


def valuacion(f: dict | None, tesis: dict | None, cubeta: str | None) -> dict:
    """Valor intrinseco conservador, margen requerido, precio maximo de compra y
    estado de valor. `tesis` = {valor_intrinseco_min/max, margen_seguridad_req_pct}
    (None para una candidata sin tesis)."""
    tesis = tesis or {}
    out = {"precio_usd": None, "graham": None, "manual_min": tesis.get("valor_intrinseco_min"),
           "manual_max": tesis.get("valor_intrinseco_max"), "vi_conservador": None, "vi_origen": None,
           "margen_req_pct": None, "margen_req_origen": None, "precio_maximo_compra": None,
           "margen_actual_pct": None, "estado": "sin_verificar", "motivo": None}
    if f is None:
        out["motivo"] = "sin fundamentales cargados"
        return out
    if f.get("tipo_instrumento") == "etf":
        out["estado"], out["motivo"] = "no_aplica", "ETF: no tiene fundamentales de empresa"
        return out

    out["precio_usd"] = f.get("precio_subyacente_usd")
    out["graham"] = numero_de_graham(f.get("eps_ttm"), f.get("bvps"))
    candidatos = []
    if cubeta == "valor_puro" and out["graham"] is not None:
        candidatos.append((out["graham"], "graham"))
    if out["manual_min"] is not None:
        candidatos.append((out["manual_min"], "manual"))
    if candidatos:
        out["vi_conservador"], out["vi_origen"] = min(candidatos)

    if tesis.get("margen_seguridad_req_pct") is not None:
        out["margen_req_pct"], out["margen_req_origen"] = tesis["margen_seguridad_req_pct"], "posición"
    elif cubeta in MARGEN_REQ_DEFAULT:
        out["margen_req_pct"], out["margen_req_origen"] = MARGEN_REQ_DEFAULT[cubeta], "cubeta"

    if out["precio_usd"] is None:
        out["motivo"] = "sin precio del subyacente en USD verificado"
    elif out["vi_conservador"] is None:
        out["motivo"] = ("cargá el rango de valor intrínseco en la ficha (el Número de Graham solo se usa en la cubeta Valor puro)"
                         if cubeta != "valor_puro" else
                         "sin Número de Graham (EPS o BVPS no verificados) ni rango manual")
    elif out["margen_req_pct"] is None:
        out["motivo"] = "asigná una cubeta o cargá el margen de seguridad requerido"
    else:
        vi, precio = out["vi_conservador"], out["precio_usd"]
        out["precio_maximo_compra"] = vi * (1 - out["margen_req_pct"] / 100)
        out["margen_actual_pct"] = (1 - precio / vi) * 100
        if precio <= out["precio_maximo_compra"]:
            out["estado"] = "infravalorada"
        elif precio <= vi * VALOR_JUSTO_HASTA_PCT / 100:
            out["estado"] = "valor_justo"
        else:
            out["estado"] = "sobrevalorada"
    return out
