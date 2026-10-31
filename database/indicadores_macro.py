"""
PWOS -- Indicadores macro con historial, umbrales y objetivos (Epica 21,
2026-09-29). Nucleo compartido por el cargador (cargar_indicadores_macro.py),
cargar_benchmarks_macro.py, la API y las alertas de tesis
(alertas_umbrales.py):

- `indicadores_catalogo` (schema v2.39) es la UNICA lista de indicadores.
  De ahi salen la validacion de `tesis_umbrales.indicador` (antes un CHECK
  SQL + `INDICADORES_VALIDOS` + `_CLAVE_BENCHMARKS` + la lista de app.js,
  4 copias a mano), el mapeo a `benchmarks_macro` (`clave_benchmark`) y
  las etiquetas del front (GET /api/indicadores-catalogo).
- La serie vive en `macro_historico` (reusada y ampliada, decision del
  usuario 2026-09-29, en vez de una tabla nueva): `clave` =
  `indicadores_catalogo.id`. Se escribe SOLO con `upsert_serie`: la
  ultima version de un dato revisado por la fuente pisa a la anterior
  (FRED revisa el CPI, INDEC el EMAE), un NULL/NaN nunca pisa un valor
  existente, y cada carga informa cuantas filas existentes cambiaron.
- `calcular_metricas` es la UNICA funcion de delta 3m/12m y percentil 10
  anios -- se calcula al vuelo desde la serie, no se guarda. Tests en
  test_indicadores_macro.py.
- `ccl_mep_brecha_pct` es el unico indicador DERIVADO: no vive en
  `macro_historico`, se calcula al vuelo desde `precios` (brecha_cambiaria.py,
  mismo criterio que desde la Epica 12).

Sin FK real hacia el catalogo: las conexiones de api.py no activan
PRAGMA foreign_keys (deuda tecnica anotada aparte en ESTADO_PWOS.md), asi
que la validacion contra el catalogo se hace aca, en Python.
"""
import calendar
import json
import math
import statistics
from bisect import bisect_right
from datetime import date, datetime, timedelta

from brecha_cambiaria import brecha_ccl_mep_historico
import configuracion

TIPOS_DELTA = ("pp", "pct")
# semanal/anual: Tanda 3 (v2.41). Semanal se trata como diaria (tolerancia en
# dias); anual, por mes calendario con 2 meses de tolerancia -> su delta a
# 3 meses queda vacio (no hay dato trimestral) y el de 12 compara con el anio previo.
FRECUENCIAS = ("diaria", "semanal", "mensual", "trimestral", "anual")
OPERADORES = ("mayor_que", "menor_que")

# Hasta donde se acepta una observacion de referencia MAS VIEJA que la
# fecha objetivo del delta (nunca una posterior). Diaria: en dias (fines
# de semana, feriados, huecos cortos de la fuente). Mensual/trimestral:
# en meses calendario -- la comparacion es por mes, no por dia, porque
# las fuentes fechan distinto (FRED el dia 1, INDEC/argentinadatos fin de
# mes) y "30-nov menos 3 meses" no puede caer en un mes equivocado.
TOLERANCIA_DIAS_DIARIA = 10
# Semanal: 17 dias = hasta 2 semanas sin dato (FactSet publica algunas
# semanas una edicion reducida sin el P/E -- hueco real de la fuente, no se inventa).
TOLERANCIA_DIAS = {"diaria": TOLERANCIA_DIAS_DIARIA, "semanal": 17}
TOLERANCIA_MESES = {"mensual": 1, "trimestral": 2, "anual": 2}

VENTANA_PERCENTIL_MESES = 120  # 10 anios

# Series de empleo de EEUU (v2.47) que 2020 deforma (nominas -20 millones en
# un mes, subsidios > 6 millones): el percentil se calcula igual y la tarjeta
# lo avisa mientras la ventana de 10 anios incluya 2020.
NOTA_VENTANA_2020 = ("nominas_eeuu_prom3m", "subsidios_desempleo_4s", "participacion_eeuu")

# Comparacion de "cambio de valor" en upsert_serie: redondeo a 4
# decimales, la misma precision con la que append_macro_historico
# guardaba hasta la Epica 21 -- asi una recarga de datos viejos con mas
# decimales no se cuenta como "revision" de la fuente.
DECIMALES_COMPARACION = 4


# ---------------------------------------------------------------- utilidades
def restar_meses(d: date, meses: int) -> date:
    """`d` menos `meses` meses calendario; el dia se recorta al ultimo del
    mes destino (31-may menos 3 meses = 28/29-feb)."""
    total = d.year * 12 + (d.month - 1) - meses
    anio, mes0 = divmod(total, 12)
    ultimo = calendar.monthrange(anio, mes0 + 1)[1]
    return date(anio, mes0 + 1, min(d.day, ultimo))


def _indice_mes(d: date) -> int:
    return d.year * 12 + d.month - 1


def fecha_iso(f) -> str:
    """Normaliza date / datetime / pandas.Timestamp / str a 'YYYY-MM-DD'."""
    if isinstance(f, str):
        return date.fromisoformat(f[:10]).isoformat()
    if isinstance(f, datetime) or hasattr(f, "to_pydatetime"):
        return f.date().isoformat()
    if isinstance(f, date):
        return f.isoformat()
    raise ValueError(f"fecha no reconocida: {f!r}")


def float_limpio(v) -> float | None:
    """float nativo de Python (nunca numpy -- leccion del BLOB), o None si
    el valor es None/NaN/infinito/no numerico."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _serie_limpia(serie) -> tuple[list[date], list[float]]:
    """Ordena por fecha, descarta valores invalidos y deja 1 valor por
    fecha (el ultimo que aparece)."""
    por_fecha: dict[date, float] = {}
    for f, v in serie:
        x = float_limpio(v)
        if x is None:
            continue
        por_fecha[date.fromisoformat(fecha_iso(f))] = x
    fechas = sorted(por_fecha)
    return fechas, [por_fecha[f] for f in fechas]


# ---------------------------------------------------- delta 3m/12m y percentil
def _referencia(fechas: list[date], fecha_actual: date, meses: int, frecuencia: str) -> int | None:
    """Indice de la observacion de referencia para el delta a `meses`:
    la mas reciente que NO pasa hacia adelante de fecha_actual - meses,
    dentro de la tolerancia de su frecuencia. None si no hay."""
    objetivo = restar_meses(fecha_actual, meses)
    if frecuencia in ("diaria", "semanal"):
        i = bisect_right(fechas, objetivo) - 1
        if i < 0 or (objetivo - fechas[i]).days > TOLERANCIA_DIAS[frecuencia]:
            return None
        return i
    mes_objetivo = _indice_mes(objetivo)
    candidato = None
    for i, f in enumerate(fechas):
        if _indice_mes(f) <= mes_objetivo:
            candidato = i
        else:
            break
    if candidato is None or mes_objetivo - _indice_mes(fechas[candidato]) > TOLERANCIA_MESES[frecuencia]:
        return None
    return candidato


def _delta(actual: float, ref: float, tipo_delta: str) -> float | None:
    if tipo_delta == "pp":
        return actual - ref
    # pct: variacion porcentual. Sin sentido con base <= 0 (todos los
    # indicadores `pct` del catalogo son niveles positivos).
    if ref <= 0:
        return None
    return (actual / ref - 1) * 100


def percentil(valores: list[float], x: float) -> float | None:
    """Posicion de `x` dentro de `valores` (que lo incluye): 0 = minimo,
    100 = maximo. Empates contados a la mitad (rango medio), asi una
    serie constante da 50. None con menos de 2 observaciones."""
    n = len(valores)
    if n < 2:
        return None
    menores = sum(1 for v in valores if v < x)
    iguales = sum(1 for v in valores if v == x)
    return 100 * (menores + 0.5 * (iguales - 1)) / (n - 1)


def calcular_metricas(serie, tipo_delta: str, frecuencia: str) -> dict:
    """`serie`: iterable de (fecha, valor). Devuelve valor actual (ultima
    observacion, con SU fecha), delta a 3 y 12 meses (con la fecha de la
    observacion contra la que se comparo) y percentil del valor actual
    en la ventana de 10 anios que termina en la fecha actual. Si hay
    menos de 10 anios de historia, el percentil usa lo que haya y
    `historia_corta` = True (la UI muestra "n anios de historia")."""
    if tipo_delta not in TIPOS_DELTA:
        raise ValueError(f"tipo_delta invalido: {tipo_delta!r}")
    if frecuencia not in FRECUENCIAS:
        raise ValueError(f"frecuencia invalida: {frecuencia!r}")

    fechas, valores = _serie_limpia(serie)
    vacio = {
        "valor": None, "fecha": None,
        "delta_3m": None, "fecha_ref_3m": None,
        "delta_12m": None, "fecha_ref_12m": None,
        "percentil_10a": None, "mediana_10a": None, "n_obs_ventana": 0,
        "anios_historia": None, "historia_corta": None,
    }
    if not fechas:
        return vacio

    fecha_actual, actual = fechas[-1], valores[-1]
    out = dict(vacio, valor=actual, fecha=fecha_actual.isoformat())

    for meses, sufijo in ((3, "3m"), (12, "12m")):
        i = _referencia(fechas, fecha_actual, meses, frecuencia)
        if i is not None:
            out[f"delta_{sufijo}"] = _delta(actual, valores[i], tipo_delta)
            out[f"fecha_ref_{sufijo}"] = fechas[i].isoformat()

    inicio_ventana = restar_meses(fecha_actual, VENTANA_PERCENTIL_MESES)
    ventana = [v for f, v in zip(fechas, valores) if f >= inicio_ventana]
    out["percentil_10a"] = percentil(ventana, actual)
    # Mediana de la misma ventana (Tanda 4): contexto, no
    # decide colores. Mediana y no media: no la deforman los extremos.
    out["mediana_10a"] = statistics.median(ventana) if ventana else None
    out["n_obs_ventana"] = len(ventana)
    primera_en_ventana = max(fechas[0], inicio_ventana)
    out["anios_historia"] = round((fecha_actual - primera_en_ventana).days / 365.25, 1)
    # Historia corta = la serie arranca DESPUES del inicio de la ventana
    # (con el mismo margen que el delta, para no marcar como "corta" una
    # mensual que arranca el 1ro de un mes y la ventana el 30).
    margen = timedelta(days=TOLERANCIA_DIAS[frecuencia] if frecuencia in TOLERANCIA_DIAS
                       else 31 * TOLERANCIA_MESES[frecuencia])
    out["historia_corta"] = fechas[0] > inicio_ventana + margen
    return out


# ---------------------------------------------------------------- catalogo
_COLS_CATALOGO = ("id", "nombre", "seccion", "unidad", "tipo_delta", "frecuencia", "fuente",
                  "serie_origen", "prioridad", "activo", "orden", "clave_benchmark", "decimales", "semaforo")


def catalogo(con, solo_activos: bool = True) -> list[dict]:
    where = "WHERE activo = 1" if solo_activos else ""
    filas = con.execute(
        f"SELECT {', '.join(_COLS_CATALOGO)} FROM indicadores_catalogo {where} ORDER BY orden, id;"
    ).fetchall()
    return [dict(zip(_COLS_CATALOGO, f)) for f in filas]


def indicador(con, indicador_id: str) -> dict | None:
    fila = con.execute(
        f"SELECT {', '.join(_COLS_CATALOGO)} FROM indicadores_catalogo WHERE id = ?;", (indicador_id,)
    ).fetchone()
    return dict(zip(_COLS_CATALOGO, fila)) if fila else None


def validar_indicador(con, indicador_id: str, solo_activos: bool = True) -> dict:
    """Devuelve la fila del catalogo o levanta ValueError. Reemplaza el
    CHECK hardcodeado de tesis_umbrales.indicador (Epica 21)."""
    ind = indicador(con, indicador_id)
    if ind is None or (solo_activos and not ind["activo"]):
        raise ValueError(f"indicador invalido: {indicador_id!r} (no esta activo en indicadores_catalogo)")
    return ind


def id_por_clave_benchmark(con, clave_benchmark: str) -> str | None:
    fila = con.execute(
        "SELECT id FROM indicadores_catalogo WHERE clave_benchmark = ?;", (clave_benchmark,)
    ).fetchone()
    return fila[0] if fila else None


# ---------------------------------------------------------------- serie
def _serie_brecha(con, desde=None, hasta=None) -> list[tuple[str, float]]:
    return [(r["fecha"], r["brecha_pct"]) for r in brecha_ccl_mep_historico(con, desde, hasta)]


def _serie_prima_riesgo(con, desde=None, hasta=None) -> list[tuple[str, float]]:
    """Prima de riesgo de acciones (Tanda 3, #31) = rendimiento de las
    ganancias esperadas (100 / P/E forward) - tasa real 10 anios (TIPS), en
    cada fecha con P/E, contra la TIPS de ese dia o la habil mas cercana
    hacia atras (hasta 10 dias; si no hay, esa fecha no se calcula)."""
    pe = leer_serie(con, "pe_forward", desde, hasta)
    if not pe:
        return []
    tips = leer_serie(con, "tips_10y", (date.fromisoformat(pe[0][0]) - timedelta(days=15)).isoformat(), hasta)
    fechas_t = [date.fromisoformat(f) for f, _ in tips]
    out = []
    for f, v in pe:
        d = date.fromisoformat(f)
        i = bisect_right(fechas_t, d) - 1
        if v and v > 0 and i >= 0 and (d - fechas_t[i]).days <= TOLERANCIA_DIAS_DIARIA:
            out.append((f, 100 / v - tips[i][1]))
    return out


def _serie_merval_usd(con, desde=None, hasta=None) -> list[tuple[str, float]]:
    """Merval en dolares CCL (ajuste de la Epica 21, 2026-09-30: el Merval
    en pesos nominales daba siempre un percentil alto por la inflacion) =
    ^MERV / USD_CCL de `precios` (diaria desde 2013), contra el CCL del
    mismo dia o el habil anterior (hasta 5 dias; si no hay, no se calcula)."""
    merval = leer_serie(con, "merval", desde, hasta)
    if not merval:
        return []
    condiciones, params = ["a.ticker = 'USD_CCL'", "p.precio > 0", "p.fecha >= ?"], [
        (date.fromisoformat(merval[0][0]) - timedelta(days=7)).isoformat()]
    if hasta:
        condiciones.append("p.fecha <= ?")
        params.append(hasta)
    ccl = con.execute(
        f"SELECT p.fecha, p.precio FROM precios p JOIN activos a ON a.id = p.activo_id "
        f"WHERE {' AND '.join(condiciones)} ORDER BY p.fecha;", params).fetchall()
    fechas_c = [date.fromisoformat(f) for f, _ in ccl]
    out = []
    for f, v in merval:
        d = date.fromisoformat(f)
        i = bisect_right(fechas_c, d) - 1
        if i >= 0 and (d - fechas_c[i]).days <= 5:
            out.append((f, v / ccl[i][1]))
    return out


# Indicadores sin fila en macro_historico: su serie se calcula al vuelo.
DERIVADOS = {"ccl_mep_brecha_pct": _serie_brecha, "prima_riesgo_acciones": _serie_prima_riesgo,
             "merval_usd_ccl": _serie_merval_usd}


def leer_serie(con, indicador_id: str, desde: str | None = None, hasta: str | None = None) -> list[tuple[str, float]]:
    if indicador_id in DERIVADOS:
        return DERIVADOS[indicador_id](con, desde, hasta)
    condiciones, params = ["clave = ?"], [indicador_id]
    if desde:
        condiciones.append("fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("fecha <= ?")
        params.append(hasta)
    return con.execute(
        f"SELECT fecha, valor FROM macro_historico WHERE {' AND '.join(condiciones)} ORDER BY fecha;", params
    ).fetchall()


def upsert_serie(con, indicador_id: str, puntos) -> dict:
    """Carga idempotente de observaciones en `macro_historico`.

    - Solo indicadores del catalogo (activos o no) y nunca un DERIVADO.
    - Un valor None/NaN/infinito se DESCARTA: nunca pisa un valor
      existente (condicion del usuario, 2026-09-29).
    - Fila nueva -> INSERT. Fila existente con valor distinto (a 4
      decimales) -> UPDATE con `actualizado_en`, y se cuenta como
      `filas_actualizadas` (la fuente reviso el dato). Igual -> no se toca.
    - INSERT ... ON CONFLICT DO UPDATE, nunca INSERT OR IGNORE (que
      silencia violaciones de CHECK, leccion documentada).

    No hace commit: el llamador maneja la transaccion."""
    if indicador_id in DERIVADOS:
        raise ValueError(f"{indicador_id!r} es derivado (se calcula al vuelo), no se guarda")
    validar_indicador(con, indicador_id, solo_activos=False)

    limpios: dict[str, float] = {}
    descartadas = 0
    for f, v in puntos:
        x = float_limpio(v)
        if x is None:
            descartadas += 1
            continue
        limpios[fecha_iso(f)] = round(x, 6)

    resumen = {"indicador": indicador_id, "filas_nuevas": 0, "filas_actualizadas": 0,
               "sin_cambio": 0, "descartadas": descartadas,
               "ultima_fecha": max(limpios) if limpios else None, "cambios": [], "error": None}
    if not limpios:
        return resumen

    existentes = dict(con.execute(
        "SELECT fecha, valor FROM macro_historico WHERE clave = ? AND fecha BETWEEN ? AND ?;",
        (indicador_id, min(limpios), max(limpios)),
    ).fetchall())

    filas = []
    for f in sorted(limpios):
        nuevo, viejo = limpios[f], existentes.get(f)
        if viejo is None:
            resumen["filas_nuevas"] += 1
        elif round(viejo, DECIMALES_COMPARACION) == round(nuevo, DECIMALES_COMPARACION):
            resumen["sin_cambio"] += 1
            continue
        else:
            resumen["filas_actualizadas"] += 1
            resumen["cambios"].append({"fecha": f, "antes": viejo, "despues": nuevo})
        filas.append((indicador_id, f, nuevo))

    con.executemany(
        """
        INSERT INTO macro_historico (clave, fecha, valor, actualizado_en)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (clave, fecha) DO UPDATE SET
            valor = excluded.valor,
            actualizado_en = CURRENT_TIMESTAMP;
        """,
        filas,
    )
    # Solo las ultimas 10 revisiones (las mas recientes son las que importan).
    resumen["cambios"] = resumen["cambios"][-10:]
    return resumen


# ---------------------------------------------------------------- anotaciones
_COLS_ANOTACION = ("umbral_valor", "umbral_direccion", "objetivo_valor", "nota",
                   "umbral_d3m_valor", "umbral_d3m_direccion", "actualizado")

# Centinela de guardar_anotacion: "el umbral de velocidad no vino en el
# pedido" (se conserva el guardado), distinto de None ("vino vacio" = borrar).
NO_ENVIADO = object()


def anotaciones(con) -> dict[str, dict]:
    filas = con.execute(
        f"SELECT indicador_id, {', '.join(_COLS_ANOTACION)} FROM indicadores_anotaciones;"
    ).fetchall()
    return {f[0]: dict(zip(_COLS_ANOTACION, f[1:])) for f in filas}


def _umbral_limpio(valor, direccion, campo: str, etiqueta: str) -> tuple[float | None, str | None]:
    """Valor + direccion de un umbral: vacios -> None, direccion valida, y
    van juntos (los dos o ninguno)."""
    valor = float_limpio(valor) if valor not in (None, "") else None
    direccion = direccion or None
    if direccion is not None and direccion not in OPERADORES:
        raise ValueError(f"{campo}_direccion invalida: {direccion!r} (validas: {OPERADORES})")
    if (valor is None) != (direccion is None):
        raise ValueError(f"el {etiqueta} necesita valor Y direccion (o ninguno de los dos)")
    return valor, direccion


def guardar_anotacion(con, indicador_id: str, *, umbral_valor=None, umbral_direccion=None,
                      objetivo_valor=None, nota=None,
                      umbral_d3m_valor=NO_ENVIADO, umbral_d3m_direccion=NO_ENVIADO) -> dict | None:
    """Guarda (UPSERT) lo que el usuario escribe sobre un indicador. Todo vacio
    = borra la anotacion. Cada umbral (nivel y velocidad) va con valor y
    direccion juntos. Si el umbral de velocidad no viene (NO_ENVIADO en los
    dos campos, ej. una pantalla vieja), se conserva el guardado; para
    borrarlo se mandan los dos en None. No hace commit."""
    validar_indicador(con, indicador_id)
    umbral_valor, umbral_direccion = _umbral_limpio(umbral_valor, umbral_direccion, "umbral", "umbral")
    objetivo_valor = float_limpio(objetivo_valor) if objetivo_valor not in (None, "") else None
    nota = (nota or "").strip() or None

    if umbral_d3m_valor is NO_ENVIADO and umbral_d3m_direccion is NO_ENVIADO:
        previa = anotaciones(con).get(indicador_id) or {}
        umbral_d3m_valor = previa.get("umbral_d3m_valor")
        umbral_d3m_direccion = previa.get("umbral_d3m_direccion")
    else:
        umbral_d3m_valor = None if umbral_d3m_valor is NO_ENVIADO else umbral_d3m_valor
        umbral_d3m_direccion = None if umbral_d3m_direccion is NO_ENVIADO else umbral_d3m_direccion
    umbral_d3m_valor, umbral_d3m_direccion = _umbral_limpio(
        umbral_d3m_valor, umbral_d3m_direccion, "umbral_d3m", "umbral de velocidad")

    if umbral_valor is None and objetivo_valor is None and nota is None and umbral_d3m_valor is None:
        con.execute("DELETE FROM indicadores_anotaciones WHERE indicador_id = ?;", (indicador_id,))
        return None

    con.execute(
        """
        INSERT INTO indicadores_anotaciones
            (indicador_id, umbral_valor, umbral_direccion, objetivo_valor, nota,
             umbral_d3m_valor, umbral_d3m_direccion, actualizado)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (indicador_id) DO UPDATE SET
            umbral_valor = excluded.umbral_valor,
            umbral_direccion = excluded.umbral_direccion,
            objetivo_valor = excluded.objetivo_valor,
            nota = excluded.nota,
            umbral_d3m_valor = excluded.umbral_d3m_valor,
            umbral_d3m_direccion = excluded.umbral_d3m_direccion,
            actualizado = CURRENT_TIMESTAMP;
        """,
        (indicador_id, umbral_valor, umbral_direccion, objetivo_valor, nota,
         umbral_d3m_valor, umbral_d3m_direccion),
    )
    return anotaciones(con).get(indicador_id)


def cumple(operador: str, valor_actual: float, valor_umbral: float) -> bool:
    """Misma regla para los umbrales de tesis (alertas_umbrales.py) y los
    de la tarjeta del indicador: estricto, igual al umbral no cruza."""
    return valor_actual > valor_umbral if operador == "mayor_que" else valor_actual < valor_umbral


def umbral_cruzado(valor: float | None, anotacion: dict | None) -> bool:
    if valor is None or not anotacion or anotacion.get("umbral_valor") is None:
        return False
    return cumple(anotacion["umbral_direccion"], valor, anotacion["umbral_valor"])


def umbral_d3m_cruzado(delta_3m: float | None, anotacion: dict | None) -> bool:
    """Umbral de velocidad (v2.44): contra el delta a 3 meses, en la unidad
    del `tipo_delta` (pct = variacion %, pp = diferencia en la unidad del
    indicador). Misma regla estricta que el de nivel. Sin delta (historia
    corta o sin referencia dentro de la tolerancia) no cruza.
    El delta se redondea a 6 decimales antes de comparar: es una cuenta, no
    un valor guardado, y el ruido binario rompe "igual no cruza" (ej.
    38000/40000 da -5.000000000000004 %, que es "menor que -5")."""
    if delta_3m is None or not anotacion or anotacion.get("umbral_d3m_valor") is None:
        return False
    return cumple(anotacion["umbral_d3m_direccion"], round(delta_3m, 6), anotacion["umbral_d3m_valor"])


# ---------------------------------------------------------------- maquina economica
SEMAFOROS = (("productividad", "Productividad"), ("ciclo_corto", "Ciclo corto"),
             ("ciclo_largo", "Ciclo largo"), ("valuacion", "Valuación"))
_GRAVEDAD = {"rojo": 3, "amarillo": 2, "verde": 1}


def color_indicador(valor: float | None, anotacion: dict | None) -> str:
    """Color de un indicador segun lo que escribio el usuario (regla acordada el
    2026-09-30, Tanda 4 de la Epica 21):
      - sin umbral (o sin dato)   -> 'gris' (no cuenta para el semaforo)
      - umbral cruzado            -> 'rojo'
      - sin objetivo              -> 'verde'
      - del lado bueno del objetivo -> 'verde'; entre objetivo y umbral -> 'amarillo'.
    El "lado bueno" lo define la direccion del umbral: umbral 'mayor_que'
    (malo cuando sube) -> bueno es <= objetivo; 'menor_que' -> >= objetivo."""
    if valor is None or not anotacion or anotacion.get("umbral_valor") is None:
        return "gris"
    if umbral_cruzado(valor, anotacion):
        return "rojo"
    objetivo = anotacion.get("objetivo_valor")
    if objetivo is None:
        return "verde"
    bueno = valor <= objetivo if anotacion["umbral_direccion"] == "mayor_que" else valor >= objetivo
    return "verde" if bueno else "amarillo"


def ultima_tesis_cartera(con) -> dict | None:
    fila = con.execute(
        "SELECT id, fecha, tesis FROM tesis_cartera WHERE estado = 'vigente' ORDER BY fecha DESC, id DESC LIMIT 1;"
    ).fetchone()
    return {"id": fila[0], "fecha": fila[1], "tesis": fila[2]} if fila else None


def maquina_economica(con, indicadores: list[dict] | None = None) -> dict:
    """Los 4 semaforos del panel: color = el PEOR de sus indicadores con
    color (los grises no cuentan; si todos son grises, gris), con el
    indicador que lo define, la ultima fase escrita por el usuario y el link a
    la tesis vigente mas reciente de la bitacora de la cartera."""
    indicadores = indicadores if indicadores is not None else listar_indicadores(con)
    fases = {s: f for s, f in (
        (r[0], {"fecha": r[1], "fase": r[2]}) for r in con.execute(
            """
            SELECT f.semaforo, f.fecha, f.fase FROM maquina_fases f
            WHERE f.id = (SELECT id FROM maquina_fases g WHERE g.semaforo = f.semaforo
                          ORDER BY g.fecha DESC, g.id DESC LIMIT 1);
            """))}
    revisiones = dict(con.execute("SELECT semaforo, COUNT(*) FROM maquina_fases GROUP BY semaforo;").fetchall())
    semaforos = []
    for sid, nombre in SEMAFOROS:
        miembros = [{"id": i["id"], "nombre": i["nombre"], "color": i["color"], "valor": i["valor"],
                     "fecha": i["fecha"], "unidad": i["unidad"], "decimales": i["decimales"]}
                    for i in indicadores if i.get("semaforo") == sid]
        con_color = [m for m in miembros if m["color"] != "gris"]
        define = max(con_color, key=lambda m: _GRAVEDAD[m["color"]]) if con_color else None
        semaforos.append({
            "id": sid, "nombre": nombre, "color": define["color"] if define else "gris",
            "define": define, "indicadores": miembros,
            "fase": fases.get(sid), "revisiones": revisiones.get(sid, 0),
        })
    return {"semaforos": semaforos, "tesis_macro": ultima_tesis_cartera(con)}


def guardar_fase(con, semaforo: str, fase: str, fecha: str | None = None) -> int:
    """Nueva revision de la fase de un semaforo (nunca pisa la anterior). No hace commit."""
    if semaforo not in dict(SEMAFOROS):
        raise ValueError(f"semaforo invalido: {semaforo!r} (validos: {[s for s, _ in SEMAFOROS]})")
    fase = (fase or "").strip()
    if not fase:
        raise ValueError("la fase no puede estar vacia")
    fecha = fecha_iso(fecha) if fecha else configuracion.hoy().isoformat()
    cur = con.execute("INSERT INTO maquina_fases (semaforo, fecha, fase) VALUES (?, ?, ?);", (semaforo, fecha, fase))
    return cur.lastrowid


def listar_fases(con, semaforo: str) -> list[dict]:
    if semaforo not in dict(SEMAFOROS):
        raise ValueError(f"semaforo invalido: {semaforo!r}")
    return [{"id": r[0], "fecha": r[1], "fase": r[2], "creado_en": r[3]} for r in con.execute(
        "SELECT id, fecha, fase, creado_en FROM maquina_fases WHERE semaforo = ? ORDER BY fecha DESC, id DESC;",
        (semaforo,))]


# ---------------------------------------------------------------- lecturas
def valores_actuales(con) -> dict[str, dict]:
    """Ultima observacion de CADA indicador con historial (1 consulta) +
    los derivados. Usado por alertas_umbrales.py -- reemplaza el mapeo
    a mano `_CLAVE_BENCHMARKS`."""
    out = {
        clave: {"valor": valor, "fecha": fecha}
        for clave, fecha, valor in con.execute(
            """
            SELECT m.clave, m.fecha, m.valor FROM macro_historico m
            JOIN (SELECT clave, MAX(fecha) AS f FROM macro_historico GROUP BY clave) u
              ON u.clave = m.clave AND u.f = m.fecha;
            """
        )
    }
    for ind_id in DERIVADOS:
        serie = leer_serie(con, ind_id)
        if serie:
            out[ind_id] = {"valor": serie[-1][1], "fecha": serie[-1][0]}
    return out


def _detalle_extra(con, ind: dict, serie, benchmarks: dict) -> dict | None:
    """Contexto que no es la serie en si: horizontes del REM y
    probabilidades de la Fed (detalle_json de benchmarks_macro), MEP/CCL
    de la brecha, acumulado de 20 ruedas de las compras del BCRA."""
    if ind["id"] == "ccl_mep_brecha_pct":
        filas = brecha_ccl_mep_historico(con, desde=(configuracion.hoy() - timedelta(days=30)).isoformat())
        return {"mep": filas[-1]["mep"], "ccl": filas[-1]["ccl"]} if filas else None
    if ind["id"] == "prima_riesgo_acciones" and serie:
        pe = leer_serie(con, "pe_forward", desde=serie[-1][0], hasta=serie[-1][0])
        if pe:
            rendimiento = 100 / pe[-1][1]
            return {"pe_forward": pe[-1][1], "rendimiento_ganancias": rendimiento,
                    "tips_10y": rendimiento - serie[-1][1]}
        return None
    if ind["id"] == "compras_bcra":
        ultimas = [v for _, v in serie[-20:] if v is not None]
        if not ultimas:
            return None
        return {"acumulado_20_ruedas": sum(ultimas), "desde": serie[-len(ultimas)][0]}
    clave = ind.get("clave_benchmark")
    if clave and clave in benchmarks and benchmarks[clave]:
        return json.loads(benchmarks[clave])
    return None


def _nota_ventana(indicador_id: str, fecha: str | None) -> str | None:
    if indicador_id not in NOTA_VENTANA_2020 or not fecha:
        return None
    inicio = restar_meses(date.fromisoformat(fecha), VENTANA_PERCENTIL_MESES)
    return "la ventana de 10 años incluye 2020" if inicio <= date(2020, 12, 31) else None


def listar_indicadores(con) -> list[dict]:
    """Todo lo que muestra la tarjeta de cada indicador activo: catalogo +
    valor/fecha/deltas/percentil + anotacion + umbrales cruzados (nivel,
    velocidad y el OR de los dos)."""
    anot = anotaciones(con)
    benchmarks = dict(con.execute("SELECT clave, detalle_json FROM benchmarks_macro;").fetchall())
    out = []
    for ind in catalogo(con):
        serie = leer_serie(con, ind["id"])
        metricas = calcular_metricas(serie, ind["tipo_delta"], ind["frecuencia"])
        a = anot.get(ind["id"])
        nivel = umbral_cruzado(metricas["valor"], a)
        velocidad = umbral_d3m_cruzado(metricas["delta_3m"], a)
        out.append({
            **ind,
            **metricas,
            "anotacion": a,
            "umbral_nivel_cruzado": nivel,
            "umbral_d3m_cruzado": velocidad,
            # OR de los dos: lo que ya lo usaba (borde de alerta) sigue igual.
            "umbral_cruzado": nivel or velocidad,
            # El color del semaforo sigue saliendo SOLO del nivel (decision del
            # usuario 2026-10-02; si se integra la velocidad, se decide aparte).
            "color": color_indicador(metricas["valor"], a),
            "detalle": _detalle_extra(con, ind, serie, benchmarks),
            "nota_ventana": _nota_ventana(ind["id"], metricas["fecha"]),
        })
    return out
