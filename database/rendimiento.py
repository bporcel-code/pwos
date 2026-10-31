"""
PWOS -- Rendimiento total de la cartera en el tiempo (Modified Dietz),
Tarea nueva 2026-08-21. Ver ESTADO_PWOS.md (relevamiento previo,
2026-08-21) para el panorama de datos que motivo este diseno.

Formula (estandar cuando no hay valuacion diaria, sin flujos internos):

    R = (EMV - BMV - CF) / (BMV + Sum(CF_i * W_i))

  BMV/EMV = patrimonio total (efectivo + invertido, USD) al inicio/fin
  del periodo. CF_i = cada flujo EXTERNO (deposito/extraccion real
  banco<->ecosistema de brokers) dentro del periodo. W_i = fraccion del
  periodo que ese flujo estuvo "adentro" (dias_totales - dia_del_flujo)
  / dias_totales. Compra/venta/renta/amortizacion NO son flujo externo
  -- la plata sigue en el sistema, cambia de forma, ya esta reflejada
  en BMV/EMV sin tratamiento especial.

Limitacion real, no un detalle menor: `precios` solo tiene 3 fechas con
foto COMPLETA de cartera hoy (2026-07-14, 2026-07-29, 2026-08-21) --
antes de eso no hay forma de valorizar renta fija (ver relevamiento).
Cada punto de la serie se calcula SOLO entre fechas con foto completa
real -- nunca se aproxima ni se interpola un precio faltante. Si un
activo vigente no tiene precio exacto en la fecha pedida,
`patrimonio_a_fecha` devuelve `completo=False` con la lista de lo que
falta, y el punto de la serie queda marcado incompleto en vez de
mostrar un numero que parece solido sin serlo.
"""

from datetime import date, timedelta

import configuracion

from valorizacion import valor_posicion_nativo, convertir_a_usd, convertir_efectivo_a_usd, pct_de, pct_propiedad_por_cuenta, TIPOS_CCL


# Piso de patrimonio base para que una fecha pueda anclar Modified
# Dietz (2026-08-26, ver CAMBIO en patrimonio_a_fecha). Valor elegido a
# mano, no derivado de un calculo -- USD 1.000 es lo bastante chico
# para no descartar anclas reales de una cartera modesta, y lo bastante
# grande para que un aporte tipico (cientos de USD) no domine el
# denominador de la formula. Si en el futuro aparece una ancla real
# justo debajo de este piso, es ajustable aca, no hay que tocar el
# resto de la logica.
PATRIMONIO_MINIMO_ANCLA_USD = 1000.0

# Patrones de texto que identifican un flujo EXTERNO real (deposito o
# extraccion entre el banco y el ecosistema de brokers) -- confirmados
# contra el propio codigo de clasificacion de cada importador (no texto
# libre adivinado), ver ESTADO_PWOS.md 2026-08-21 para el detalle
# broker por broker. El signo sale de `movimientos.tipo` (ingreso=+1,
# egreso=-1), no del patron -- mas robusto que inferirlo del texto.
#
# A diferencia de renta/amortizacion, ninguno de estos patrones
# necesito el filtro `NOT LIKE '[Pesos]%'` que hizo falta en un broker para
# amortizacion: verificado 2026-08-21 que "Ingreso/Retiro de Fondos"
# nunca aparece como fila espejo de un evento en otra moneda (esa fila
# ARS es casi siempre el UNICO deposito/extraccion real de esa fecha,
# no una retencion de un cobro en USD) -- confirmado tanto por los
# datos (29 filas, sin pares sospechosos de magnitud chica-vs-grande el
# mismo dia) como por el codigo (`CANTIDAD_EXCLUIDA`/las categorias
# `retiro_fondos`/`ingreso_fondos` en el dry run del broker nunca entran en el
# mecanismo de exclusion que si aplica a `renta_titulo`). No asumir que
# esto se mantiene si en el futuro cambia el formato del export de ese broker.
PATRONES_FLUJO_EXTERNO = configuracion.get("movimientos", "patrones_flujo_externo", default=[])


def precio_fx_historico(con, moneda_fx_ticker: str, fecha: str) -> float | None:
    """Copia de dashboard.py::precio_fx_historico -- se duplica (no se
    importa de dashboard.py) para no acoplar este modulo nuevo al
    panel de ganancia/perdida, que tiene su propio ciclo de cambios."""
    row = con.execute("""
        SELECT p.precio FROM precios p JOIN activos a ON a.id = p.activo_id
        WHERE a.ticker = ? AND p.fecha = ?;
    """, (moneda_fx_ticker, fecha)).fetchone()
    return row[0] if row else None


def flujos_externos(con) -> list[dict]:
    """Todos los movimientos de deposito/extraccion externa real
    (banco <-> ecosistema de brokers) en toda la base, con su monto ya
    convertido a USD al tipo de cambio de ESA fecha puntual (mismo
    criterio que "invertido" en el panel de ganancia). `signo`: +1
    deposito (entro plata), -1 extraccion (salio plata).

    Si un flujo en ARS no tiene MEP historico cargado para su fecha
    exacta, se excluye de la lista con un aviso -- no se aproxima con
    el MEP de otro dia (mismo criterio que el resto del proyecto)."""
    filas = con.execute("""
        SELECT id, fecha, tipo, monto, moneda, descripcion
        FROM movimientos
        WHERE tipo IN ('ingreso', 'egreso') AND naturaleza = 'efectivo' AND (
    """ + " OR ".join(["descripcion LIKE ?"] * len(PATRONES_FLUJO_EXTERNO)) + """
        )
        ORDER BY fecha;
    """, PATRONES_FLUJO_EXTERNO).fetchall()

    resultado = []
    sin_fx = []
    for mov_id, fecha, tipo, monto, moneda, descripcion in filas:
        signo = 1 if tipo == "ingreso" else -1
        if moneda in ("USD", "USD_CCL"):
            monto_usd = monto
        elif moneda == "ARS":
            fx_hist = precio_fx_historico(con, "USD_MEP", fecha)
            if fx_hist is None:
                sin_fx.append((mov_id, fecha, monto, descripcion))
                continue
            monto_usd = monto / fx_hist
        else:
            sin_fx.append((mov_id, fecha, monto, descripcion))
            continue
        resultado.append({
            "movimiento_id": mov_id,
            "fecha": fecha,
            "monto_usd": round(signo * monto_usd, 2),
            "descripcion": descripcion,
        })
    if sin_fx:
        print(f"AVISO flujos_externos(): {len(sin_fx)} movimiento(s) excluido(s) por falta de MEP historico de su fecha exacta:")
        for mov_id, fecha, monto, descripcion in sin_fx:
            print(f"  id={mov_id} {fecha} {monto} -- {descripcion}")
    return resultado


def _tickers_vigentes_a_fecha(con, fecha: str) -> list[tuple]:
    """(activo_id, ticker, tipo_activo, cantidad) de todo lo que estaba
    en cartera a esa fecha (replay del ledger completo, no de la
    tenencia HOY) -- mismo metodo que vista_posiciones pero acotado a
    movimientos con fecha <= la pedida.

    El filtro de "posicion cerrada" va en un WHERE externo sobre la
    subconsulta ya materializada, NO en HAVING -- mismo bug de fondo
    que se encontro y corrigio en `vista_posiciones` (sesion
    2026-07-11): SQLite reevalua la expresion agregada del HAVING con
    un redondeo de punto flotante levemente distinto al de la columna
    de salida, y deja pasar residuos fantasma que en la columna
    materializada dan 0.0 exacto (closed positions con distintos ciclos
    de compra/venta/canje)."""
    # v2.48: + splits/contrasplits (eventos_corporativos) hasta la fecha.
    filas = con.execute("""
        SELECT * FROM (
            SELECT a.id, a.ticker, a.tipo, SUM(x.cantidad) AS cantidad
            FROM (
                SELECT oa.activo_id,
                       CASE WHEN m.tipo = 'compra_activo' THEN oa.cantidad ELSE -oa.cantidad END AS cantidad
                FROM operaciones_activos oa
                JOIN movimientos m ON m.id = oa.movimiento_id
                WHERE m.fecha <= ?
                UNION ALL
                SELECT activo_id, cantidad_despues - cantidad_antes FROM eventos_corporativos WHERE fecha <= ?
            ) x
            JOIN activos a ON a.id = x.activo_id
            GROUP BY a.id
        )
        WHERE ABS(cantidad) > 1e-6;
    """, (fecha, fecha)).fetchall()
    return filas


def _efectivo_a_fecha(con, fecha: str) -> tuple[float | None, list[str]]:
    """Efectivo total en USD a una fecha (replay de saldos por cuenta +
    conversion al MEP DE ESA FECHA). None si falta el MEP historico de
    ese dia exacto -- el ledger en si siempre esta completo, lo que
    puede faltar es el tipo de cambio."""
    mep_hist = precio_fx_historico(con, "USD_MEP", fecha)
    if mep_hist is None:
        return None, [f"USD_MEP sin cotizacion cargada para {fecha}"]

    filas = con.execute("""
        SELECT c.id, c.moneda,
               COALESCE(SUM(CASE WHEN m.cuenta_destino_id = c.id THEN m.monto END), 0)
               - COALESCE(SUM(CASE WHEN m.cuenta_origen_id = c.id THEN m.monto END), 0) AS saldo
        FROM cuentas c
        LEFT JOIN movimientos m
            ON (m.cuenta_origen_id = c.id OR m.cuenta_destino_id = c.id) AND m.fecha <= ?
        GROUP BY c.id;
    """, (fecha,)).fetchall()

    # Parte del usuario de cada cuenta (contenedores compartidos, ADR-032):
    # mismo porcentaje que el patrimonio de hoy.
    pcts = pct_propiedad_por_cuenta(con)
    total = 0.0
    for cuenta_id, moneda, saldo in filas:
        if moneda == "CARTERA":
            continue
        total += convertir_efectivo_a_usd(saldo, moneda, mep_hist, pct_de(pcts, cuenta_id))
    return total, []


def patrimonio_a_fecha(con, fecha: str) -> dict:
    """Patrimonio total (efectivo + invertido) en USD, reconstruido
    COMO SI fuera esa fecha -- replay de `movimientos` para posiciones
    y saldos, `precios` para valorizar (SOLO match exacto de fecha, sin
    aproximar). `completo=False` si falta el MEP historico de esa
    fecha o el precio exacto de algun activo vigente ese dia -- en ese
    caso `total_usd` es None, no un numero parcial que parezca
    confiable.

    CAMBIO 2026-08-26 -- `completo` tambien exige al menos UNA posicion
    real vigente ese dia (no solo efectivo). Encontrado corriendo el
    backfill de FX historico para renta/ganancia realizada
    (`backfill_fx_historico_ganancia_2026-08-20.py`, que cargo MEP/CCL
    desde 2018): sin este chequeo, un dia con la cartera 100% vacia
    (solo efectivo) calificaba como "completa" de forma
    trivial -- cero tickers vigentes ese dia, entonces el for-loop de
    abajo nunca agrega nada a `faltantes`, y `completo` daba True sin
    haber valuado nada de renta fija/variable. Esto convertia esa fecha
    en ancla de Modified Dietz, produciendo un tramo unico 2020->2026 de
    +927% que mezclaba 6 anios de historia mayormente en efectivo con la
    cartera de hoy. Deliberadamente NO se puso un piso de fecha fijo
    (mas simple, pero le pondria un techo artificial a fechas viejas que
    en el futuro SI puedan calificar de verdad, ej. si se consigue
    precio historico real de bonos) -- la condicion es sobre el
    CONTENIDO de la cartera ese dia, no sobre el calendario."""
    faltantes: list[str] = []

    efectivo_usd, motivos_efectivo = _efectivo_a_fecha(con, fecha)
    faltantes += motivos_efectivo

    mep_hist = precio_fx_historico(con, "USD_MEP", fecha)
    ccl_hist = precio_fx_historico(con, "USD_CCL", fecha)

    invertido_usd = 0.0
    tickers = _tickers_vigentes_a_fecha(con, fecha)
    for activo_id, ticker, tipo_activo, cantidad in tickers:
        precio_row = con.execute(
            "SELECT precio, moneda FROM precios WHERE activo_id = ? AND fecha = ?;",
            (activo_id, fecha),
        ).fetchone()
        if precio_row is None:
            faltantes.append(f"{ticker}: sin precio cargado para {fecha}")
            continue
        precio, moneda_precio = precio_row
        # CAMBIO 2026-08-26 -- bug latente encontrado ampliando la
        # cobertura de precios de bonos (Rava): si `moneda_precio` es
        # ARS y el activo NO requiere CCL, hacia falta MEP para
        # convertir, pero nada chequeaba si `mep_hist` era None antes
        # de dividir -- `convertir_a_usd(..., mep_hist or 0.0, ...)`
        # terminaba dividiendo por cero. Nunca goldpeaba antes porque
        # las unicas fechas candidatas con precio de bono tambien
        # tenian MEP cargado por casualidad; con mucho mas historico de
        # bonos ahora eso ya no esta garantizado. Mismo tratamiento
        # simetrico que ya existia para CCL: sin la tasa que hace
        # falta, el ticker va a `faltantes`, no se inventa un valor.
        if moneda_precio == "USD":
            pass  # ya esta en dolares, no hace falta ninguna tasa
        elif tipo_activo in TIPOS_CCL:
            if ccl_hist is None:
                faltantes.append(f"{ticker}: requiere USD_CCL de {fecha}, no cargado")
                continue
        elif mep_hist is None:
            faltantes.append(f"{ticker}: requiere USD_MEP de {fecha}, no cargado")
            continue
        valor_nativo = valor_posicion_nativo(tipo_activo, cantidad, precio)
        invertido_usd += convertir_a_usd(tipo_activo, valor_nativo, moneda_precio, mep_hist or 0.0, ccl_hist or 0.0)

    completo = not faltantes and efectivo_usd is not None and len(tickers) > 0
    if completo and (efectivo_usd + invertido_usd) < PATRIMONIO_MINIMO_ANCLA_USD:
        # CAMBIO 2026-08-26 -- encontrado extendiendo la serie hacia
        # atras con el nuevo historico de bonos (Rava): con un
        # patrimonio base MUY chico (cartera recien arrancando, ej.
        # USD 397,78 en 2021-05-19), Modified Dietz se vuelve
        # numericamente inestable si el flujo externo del periodo es
        # comparable o mayor al patrimonio base -- un tramo real dio
        # -142% (matematicamente imposible sin apalancamiento) sin que
        # los datos estuvieran mal (precios limpios, flujos con FX
        # completo). Es una limitacion conocida del metodo, no un dato
        # faltante -- por eso NO se agrega a `faltantes` (no es que
        # falte algo, es que el resultado no seria interpretable) y se
        # marca `completo=False` recien aca, al final, sobre el
        # patrimonio YA calculado.
        completo = False
        faltantes = faltantes + [
            f"patrimonio base USD {efectivo_usd + invertido_usd:.2f} por debajo del "
            f"piso de USD {PATRIMONIO_MINIMO_ANCLA_USD:.0f} -- Modified Dietz no es confiable con capital tan chico"
        ]
    return {
        "fecha": fecha,
        "completo": completo,
        "faltantes": faltantes,
        "efectivo_usd": round(efectivo_usd, 2) if efectivo_usd is not None else None,
        "invertido_usd": round(invertido_usd, 2) if completo else None,
        "total_usd": round((efectivo_usd or 0.0) + invertido_usd, 2) if completo else None,
    }


def modified_dietz(bmv: float, emv: float, flujos_periodo: list[dict], fecha_inicio: str, fecha_fin: str) -> dict:
    """Un solo periodo. `flujos_periodo`: filas de flujos_externos() con
    fecha estrictamente entre (fecha_inicio, fecha_fin] -- el flujo del
    dia de inicio ya esta reflejado en BMV, no se pesa de nuevo."""
    from datetime import date as _date

    d0 = _date.fromisoformat(fecha_inicio)
    d1 = _date.fromisoformat(fecha_fin)
    dias_totales = (d1 - d0).days
    if dias_totales <= 0:
        raise ValueError(f"periodo invalido: {fecha_inicio} a {fecha_fin}")

    cf_total = 0.0
    cf_ponderado = 0.0
    for f in flujos_periodo:
        dia_flujo = (_date.fromisoformat(f["fecha"]) - d0).days
        peso = (dias_totales - dia_flujo) / dias_totales
        cf_total += f["monto_usd"]
        cf_ponderado += f["monto_usd"] * peso

    denominador = bmv + cf_ponderado
    if abs(denominador) < 1e-9:
        rendimiento = None
    else:
        rendimiento = (emv - bmv - cf_total) / denominador

    return {
        "fecha_inicio": fecha_inicio,
        "fecha_fin": fecha_fin,
        "dias": dias_totales,
        "bmv_usd": round(bmv, 2),
        "emv_usd": round(emv, 2),
        "flujo_neto_usd": round(cf_total, 2),
        "rendimiento_pct": round(rendimiento * 100, 4) if rendimiento is not None else None,
    }


def _fechas_con_precio_completo(con) -> list[str]:
    """Prefiltro rapido (2026-08-26, ver CAMBIO abajo): de las fechas
    con al menos 1 precio cargado, cuales tienen precio para TODOS los
    tickers vigentes ese dia -- el MISMO chequeo que ya hace
    `patrimonio_a_fecha` via `precio_row is None`, pero calculado con
    un barrido cronologico en memoria (2 queries grandes) en vez de una
    query chica por ticker por fecha candidata. Estrictamente mas
    permisivo que `patrimonio_a_fecha` -- nunca descarta una fecha que
    esa funcion aceptaria (si a un ticker vigente le falta el precio
    ese dia exacto, `patrimonio_a_fecha` la rechaza igual por su propio
    chequeo) -- solo reduce CUANTAS fechas necesitan la reconstruccion
    completa y autoritativa despues."""
    eventos = con.execute("""
        SELECT m.fecha, oa.activo_id,
               CASE WHEN m.tipo = 'compra_activo' THEN oa.cantidad ELSE -oa.cantidad END
        FROM operaciones_activos oa
        JOIN movimientos m ON m.id = oa.movimiento_id
        UNION ALL
        SELECT fecha, activo_id, cantidad_despues - cantidad_antes FROM eventos_corporativos
        ORDER BY 1;
    """).fetchall()
    tiene_precio = set(con.execute("SELECT activo_id, fecha FROM precios;").fetchall())
    fechas_con_precio = [r[0] for r in con.execute(
        "SELECT DISTINCT fecha FROM precios ORDER BY fecha;"
    ).fetchall()]

    resultado = []
    idx = 0
    tenencia: dict[int, float] = {}
    for fecha in fechas_con_precio:
        while idx < len(eventos) and eventos[idx][0] <= fecha:
            _, activo_id, delta = eventos[idx]
            tenencia[activo_id] = tenencia.get(activo_id, 0.0) + delta
            idx += 1
        vigentes = [aid for aid, cantidad in tenencia.items() if abs(cantidad) > 1e-6]
        if vigentes and all((aid, fecha) in tiene_precio for aid in vigentes):
            resultado.append(fecha)
    return resultado


def fechas_ancla_disponibles(con) -> list[str]:
    """Fechas con foto de cartera COMPLETA (todo lo vigente a esa fecha
    tiene precio exacto cargado ese mismo dia) -- candidatas a anclar
    Modified Dietz. Se recalcula contra `patrimonio_a_fecha` en vez de
    un umbral fijo de "cantidad de activos con precio" (ese umbral se
    rompe solo si la cartera crece o se achica).

    CAMBIO 2026-08-26 -- pasa primero por `_fechas_con_precio_completo`
    (prefiltro rapido, no cambia el resultado final -- ver su
    docstring) antes de reconstruir con `patrimonio_a_fecha`. Antes de
    este cambio, con solo un puñado de fechas en `precios`, correr
    `patrimonio_a_fecha` para CADA UNA no hacia falta que fuera rapido.
    El backfill de precio historico de bonos via Rava sumo miles de
    fechas nuevas (la inmensa mayoria con 1 solo bono priceado, nunca
    la cartera completa) -- sin el prefiltro, `/api/rendimiento-serie`
    pasaba de instantaneo a 37+ segundos DENTRO de Docker (el I/O de
    SQLite sobre el volumen VeraCrypt via WSL2 es mucho mas lento que
    en el host para muchas queries chicas -- confirmado, 3,3s en el
    host vs. 37s en el contenedor para la MISMA logica sin prefiltro)."""
    fechas_candidatas = _fechas_con_precio_completo(con)
    return [f for f in fechas_candidatas if patrimonio_a_fecha(con, f)["completo"]]


def fecha_foto_cierre(anio: int, mes: int) -> str:
    """Dia en que se dibuja el cierre (anio, mes): el 1 del mes SIGUIENTE. La
    columna de un mes del sheet refleja la actividad de ese mes y la foto se
    toma a principios del mes siguiente (verificado 2026-10-27 contra el MEP
    diario; antes se dibujaba el dia 1 del mismo mes y la curva quedaba
    corrida un mes atras)."""
    return f"{anio + (mes == 12):04d}-{mes % 12 + 1:02d}-01"


def serie_rendimiento_mensual(con) -> list[dict]:
    """Serie de rendimiento (time-weighted, compuesto) a partir de los
    cierres mensuales MANUALES del usuario (`cierres_mensuales`, cargados
    del sheet -- ver cargar_cierres_mensuales_sheet_2026-09-03.py y
    ESTADO_PWOS.md 2026-09-03).

    FUENTE de la curva del dashboard desde 2024. Se eligio sobre la
    reconstruccion ledger+precios (`serie_rendimiento` mas abajo) porque
    esa no es confiable pre-2026: ~30 bonos/LECAPs ilíquidos ya vencidos
    sin precio en NINGUNA fuente (la API del broker tampoco los tiene, confirmado
    2026-09-03). El cierre manual es dato MEDIDO, completo (todos los
    contenedores, no solo brokers) y mensual. `serie_rendimiento` queda
    para el reporte de reconciliacion (ledger vs. cierre manual).

    Rendimiento del mes m:
        r_m = rendimiento_usd_m / patrimonio_total_usd_{m-1}
    donde rendimiento_usd_m = patrimonio_m - aportes_m - patrimonio_{m-1}
    (el aporte -- sueldo neto de gastos -- NO cuenta como rendimiento el
    mes que entra, pero SI forma parte de la base del mes siguiente).
    Aporte tratado a fin de mes (conservador, nunca infla). Encadenado
    multiplicativo desde el primer cierre.

    Devuelve la misma forma que `serie_rendimiento` (un dict por tramo)
    para que /api/rendimiento-serie y el frontend no cambien. `dias` es
    los dias calendario reales entre fotos consecutivas -- siempre << el
    UMBRAL_DIAS_TRAMO_LARGO del frontend, asi que ningun tramo se dibuja
    punteado."""
    from datetime import date as _date

    filas = con.execute(
        "SELECT anio, mes, patrimonio_total_usd, aportes_netos_usd, rendimiento_usd "
        "FROM cierres_mensuales ORDER BY anio, mes;"
    ).fetchall()
    if len(filas) < 2:
        return []

    _fecha = fecha_foto_cierre

    tramos = []
    acumulado = 1.0
    for i in range(1, len(filas)):
        a0, m0, patr0, _ap0, _r0 = filas[i - 1]
        a1, m1, patr1, ap1, rend1 = filas[i]
        f_ini, f_fin = _fecha(a0, m0), _fecha(a1, m1)
        dias = (_date.fromisoformat(f_fin) - _date.fromisoformat(f_ini)).days

        rendimiento_pct = round(rend1 / patr0 * 100, 4) if patr0 else None
        if rendimiento_pct is not None:
            acumulado *= (1 + rendimiento_pct / 100)

        tramos.append({
            "fecha_inicio": f_ini,
            "fecha_fin": f_fin,
            "dias": dias,
            "completo": True,
            "faltantes": [],
            "bmv_usd": round(patr0, 2),
            "emv_usd": round(patr1, 2),
            "flujo_neto_usd": round(ap1, 2),
            "rendimiento_pct": rendimiento_pct,
            "acumulado_pct": round((acumulado - 1) * 100, 4) if rendimiento_pct is not None else None,
            "flujos": [],
        })
    return tramos


def serie_rendimiento(con, fechas_ancla: list[str]) -> list[dict]:
    """Encadena Modified Dietz entre cada par de fechas ANCLA
    consecutivas (fechas con foto de cartera completa real, en orden
    ascendente) -- no meses de calendario, porque hoy no hay una foto
    completa en cada fin de mes (ver docstring del modulo). Cada tramo
    que no se pueda calcular por falta de datos queda con
    `rendimiento_pct=None` y el detalle de que falta, en vez de
    inventar un numero. Incluye `acumulado_pct`: rendimiento compuesto
    (multiplicativo) desde la primera fecha ancla -- se corta en el
    primer tramo incompleto (no se puede encadenar mas alla de un
    hueco real).

    NOTA (2026-09-03): ya NO es la fuente de la curva del dashboard --
    la reemplazo `serie_rendimiento_mensual` (cierres manuales). Esta
    queda para el reporte de reconciliacion ledger vs. sheet."""
    todos_los_flujos = flujos_externos(con)

    tramos = []
    acumulado = 1.0
    acumulado_roto = False
    for i in range(len(fechas_ancla) - 1):
        f_ini, f_fin = fechas_ancla[i], fechas_ancla[i + 1]
        p_ini = patrimonio_a_fecha(con, f_ini)
        p_fin = patrimonio_a_fecha(con, f_fin)

        if not p_ini["completo"] or not p_fin["completo"]:
            tramos.append({
                "fecha_inicio": f_ini, "fecha_fin": f_fin,
                "rendimiento_pct": None, "acumulado_pct": None,
                "completo": False,
                "faltantes": p_ini["faltantes"] + p_fin["faltantes"],
            })
            acumulado_roto = True
            continue

        flujos_periodo = [f for f in todos_los_flujos if f_ini < f["fecha"] <= f_fin]
        tramo = modified_dietz(p_ini["total_usd"], p_fin["total_usd"], flujos_periodo, f_ini, f_fin)
        tramo["completo"] = True
        tramo["faltantes"] = []
        tramo["bmv_detalle"] = p_ini
        tramo["emv_detalle"] = p_fin
        tramo["flujos"] = flujos_periodo

        if not acumulado_roto and tramo["rendimiento_pct"] is not None:
            acumulado *= (1 + tramo["rendimiento_pct"] / 100)
            tramo["acumulado_pct"] = round((acumulado - 1) * 100, 4)
        else:
            tramo["acumulado_pct"] = None
        tramos.append(tramo)

    return tramos


# Punto provisorio del mes en curso (2026-10-27). La curva
# firme termina en el ultimo cierre del sheet; desde ahi hasta hoy se estima
# con PWOS en los DOS extremos (mismo alcance: efectivo + invertido, con la
# parte propia de contenedores compartidos, sin inmuebles ni deuda -- igual
# que la solapa CUENTAS), asi las diferencias de alcance con el sheet se
# cancelan. Con el cierre diario guardado, no el vivo: es historia (ADR-033).
#
#   aporte = ingresos - egresos de "Movimientos y gastos" (sin las
#            transferencias entre cuentas propias que empareja la ronda)
#            + "Aporte externo" de EFECTIVO, cada uno al MEP de su dia.
#   Los pagos de una deuda que ya son egresos ahi cuentan como
#   retiro sin restar `pagos_pasivo` aparte (si no, se restarian 2 veces).
#   rendimiento = patrimonio_hoy - patrimonio_foto - aporte
#
# Un movimiento que todavia no se cargo (ronda atrasada) falta a la vez en la
# caja y en el aporte: no sesga el rendimiento. Se reemplaza solo cuando se
# carga el cierre real del mes.
DIAS_BUSCA_CIERRE_PROVISORIO = 7


def _mep_de_dia(con, fecha: str) -> float | None:
    row = con.execute(
        "SELECT p.precio FROM precios p JOIN activos a ON a.id = p.activo_id "
        "WHERE a.ticker = 'USD_MEP' AND p.fecha <= ? ORDER BY p.fecha DESC LIMIT 1;", (fecha,)
    ).fetchone()
    return row[0] if row else None


def aporte_usd_entre(con, desde: str, hasta: str) -> dict:
    """Aporte externo neto (USD) con fecha en (desde, hasta]."""
    from transferencias import PATRONES_APORTE_EXTERNO, verificar_transferencias_propias
    from movimientos_gastos import listar_movimientos_gastos

    siguiente = (date.fromisoformat(desde) + timedelta(days=1)).isoformat()
    pares = verificar_transferencias_propias(con, siguiente, hasta)
    propias = {fila[0] for par in pares["pares"] for fila in par}
    movimientos = [m for m in listar_movimientos_gastos(con)
                   if desde < m["fecha"] <= hasta and m["id"] not in propias]
    externos = con.execute(
        "SELECT fecha, tipo, monto, moneda FROM movimientos WHERE fecha > ? AND fecha <= ? AND ("
        + " OR ".join(["descripcion LIKE ?"] * len(PATRONES_APORTE_EXTERNO)) + ");",
        [desde, hasta] + PATRONES_APORTE_EXTERNO).fetchall()
    total, sin_mep = 0.0, []
    for fecha, tipo, monto, moneda in ([(m["fecha"], m["tipo"], m["monto"], m["moneda"]) for m in movimientos]
                                       + list(externos)):
        fx = 1.0 if moneda == "USD" else _mep_de_dia(con, fecha)
        if not fx:
            sin_mep.append(fecha)
            continue
        total += (monto if tipo == "ingreso" else -monto) / fx
    return {"aporte_usd": round(total, 2), "movimientos": len(movimientos) + len(externos),
            "transferencias_propias_sin_pareja": len(pares["sin_pareja"]), "sin_mep": sin_mep}


def punto_provisorio(con, hoy: date | None = None) -> dict | None:
    """Tramo provisorio desde la foto del ultimo cierre hasta el ultimo dia con
    cierre diario completo. None si no hay cierres o todavia no hay un dia
    posterior a la foto; con `rendimiento_pct` None y `motivo` si no se puede
    calcular sin adivinar."""
    hoy = hoy or configuracion.hoy()
    serie = serie_rendimiento_mensual(con)
    if not serie:
        return None
    ultimo = serie[-1]
    desde = ultimo["fecha_fin"]
    base = {"fecha_inicio": desde, "provisorio": True, "rendimiento_pct": None, "acumulado_pct": None}

    inicio = patrimonio_a_fecha(con, desde)
    if not inicio["completo"]:
        return base | {"motivo": f"PWOS no puede reconstruir el patrimonio del {desde}: {inicio['faltantes'][:3]}"}
    hasta, fin = None, None
    for i in range(DIAS_BUSCA_CIERRE_PROVISORIO + 1):
        f = (hoy - timedelta(days=i)).isoformat()
        if f <= desde:
            break
        r = patrimonio_a_fecha(con, f)
        if r["completo"]:
            hasta, fin = f, r
            break
    if hasta is None:
        return None if (hoy.isoformat() <= desde) else base | {
            "motivo": f"ningun dia completo entre {desde} y {hoy.isoformat()}"}

    aporte = aporte_usd_entre(con, desde, hasta)
    if aporte["sin_mep"]:
        return base | {"fecha_fin": hasta, "motivo": f"sin MEP para {aporte['sin_mep'][:3]}"}
    p0, p1 = inicio["total_usd"], fin["total_usd"]
    rendimiento_usd = p1 - p0 - aporte["aporte_usd"]
    pct = rendimiento_usd / p0 * 100
    acumulado = None
    if ultimo["acumulado_pct"] is not None:
        acumulado = round(((1 + ultimo["acumulado_pct"] / 100) * (1 + pct / 100) - 1) * 100, 4)
    ultima_ronda = con.execute("SELECT MAX(fecha) FROM movimientos WHERE fecha <= ?;", (hasta,)).fetchone()[0]
    return base | {
        "fecha_fin": hasta,
        "dias": (date.fromisoformat(hasta) - date.fromisoformat(desde)).days,
        "bmv_usd": round(p0, 2),
        "emv_usd": round(p1, 2),
        "flujo_neto_usd": aporte["aporte_usd"],
        "rendimiento_usd": round(rendimiento_usd, 2),
        "rendimiento_pct": round(pct, 4),
        "acumulado_pct": acumulado,
        "ultimo_movimiento": ultima_ronda,
        "motivo": None,
    }
