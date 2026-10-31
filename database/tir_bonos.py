"""
PWOS -- motor de TIR para renta fija (Epica 11, Herramienta 1: rotacion
de bonos por compresion de TIR).

Metodologia (confirmada con el usuario, 2026-09-07):
  - TIR de entrada y TIR de hoy se calculan con el MISMO motor (xirr
    propio contra `cronograma_pagos_bonos`), no se mezcla con el TIR
    que publica Rava para el mercado -- compresion 100% apples-to-apples,
    misma convencion de dias/compounding en los 2 lados.
  - Precio de entrada = `monto` real pagado (`movimientos.monto`,
    incluye comision) / `cantidad` × 100 -- NO se usa
    `operaciones_activos.precio_unitario` directo: auditoria 2026-09-07
    encontro que un importador lo guarda en convencion "por VN100" mientras
    los demas lo guardan "por VN1" (~100x mas chico) --
    inconsistencia real entre importadores, sin impacto conocido hoy
    (ningun panel ya construido lo usa) pero que rompería este calculo
    si se confiara en el campo tal cual. Derivar de monto/cantidad
    evita el problema de raiz sin migrar datos historicos.
  - `moneda_pago` 'USD' y 'ARS' directo. 'USD_LINKED' (dollar-linked,
    2026-10-14): Rava trae el cronograma en ARS (montos en USD pasados
    a pesos al A3500 del dia de la carga) y el precio de mercado es la
    especie D en USD (TICKER_OVERRIDE_DATA912). Se lleva el cronograma
    a USD con el tipo de cambio IMPLICITO del propio cronograma (ver
    fx_implicito_usd_linked) -- el mismo que uso Rava, sin depender de
    que USD_A3500 este al dia -- y se calcula todo en USD.
  - Bonos CER (`activos.tipo = 'bono_cer'`, 2026-10-14): TIR REAL en las
    2 puntas. Rava trae el cronograma ya ajustado por el CER del dia en
    que se cargo; se pasa a unidades reales dividiendo por ese CER, y el
    precio (de entrada o de hoy) por el CER de SU fecha -- ver
    cer_rezagado. Antes la TIR de entrada comparaba el precio de compra
    con flujos inflados por el CER posterior (un bono CER: 28% "real" que era
    casi nominal) y la de hoy dependia de que el cronograma estuviera
    fresco (0,73% con el cronograma de 2 semanas antes).
"""

from datetime import date, timedelta

from valorizacion import valor_posicion_nativo, convertir_a_usd, precio_actual
import clasificar_bonos
import configuracion

DIAS_ANIO = 365.0

TIPOS_RENTA_FIJA = ("bono_soberano", "bono_provincial", "bono_cer", "on", "lecap")


def van(flujos: list[tuple[date, float]], tasa: float, fecha0: date | None = None) -> float:
    """Valor actual neto de `flujos` (fecha, monto -- negativo egreso,
    positivo ingreso) a `tasa` anual, descontado desde `fecha0` (default:
    la fecha del primer flujo, `flujos[0][0]` -- asi lo usa `xirr`, y
    asi lo usaba este modulo antes de exponer `van` como funcion propia,
    sin cambio de comportamiento para `tir_al`/`xirr`). Un flujo anterior
    a `fecha0` se "capitaliza" hacia adelante en vez de descontarse
    (exponente negativo) -- formula estandar de VAN, sin caso especial.
    MISMO motor para renta fija y Real Estate (ver flujo_inmueble.py),
    no se reimplementa el descuento en un segundo lugar del proyecto."""
    ancla = fecha0 if fecha0 is not None else flujos[0][0]
    return sum(
        monto / (1 + tasa) ** ((fecha - ancla).days / DIAS_ANIO)
        for fecha, monto in flujos
    )


def xirr(flujos: list[tuple[date, float]]) -> float | None:
    """Tasa anual que hace VAN=0 sobre `flujos` (fecha, monto -- negativo
    egreso, positivo ingreso), por biseccion. None si no hay cambio de
    signo de VAN en [-99%, 1000%] (sin raiz real en un rango economico
    sensato para renta fija)."""
    if len(flujos) < 2:
        return None

    lo, hi = -0.99, 10.0
    van_lo, van_hi = van(flujos, lo), van(flujos, hi)
    if van_lo == 0:
        return lo
    if van_hi == 0:
        return hi
    if (van_lo > 0) == (van_hi > 0):
        return None  # sin cambio de signo -- no hay raiz en el rango

    for _ in range(100):
        mid = (lo + hi) / 2
        van_mid = van(flujos, mid)
        if van_mid == 0:
            return mid
        if (van_mid > 0) == (van_lo > 0):
            lo, van_lo = mid, van_mid
        else:
            hi = mid
    return (lo + hi) / 2


def tir_al(con, activo_id: int, fecha_ref: date, precio_vn100: float, divisor: float = 1.0) -> float | None:
    """TIR contra el cronograma que queda DESPUES de `fecha_ref` -- sirve
    tanto para "TIR de entrada" (fecha_ref = fecha de compra, precio =
    lo pagado) como "TIR de hoy" (fecha_ref = hoy, precio = cotizacion
    actual). None si no queda ningun pago futuro (bono ya vencido segun
    el cronograma cargado) o si xirr no converge. `divisor`: cada pago
    del cronograma se divide por este valor (USD_LINKED: el tipo de
    cambio implicito, para llevar el cronograma en ARS a USD)."""
    pagos = con.execute(
        """
        SELECT fecha_pago, monto_por_vn
        FROM cronograma_pagos_bonos
        WHERE activo_id = ? AND fecha_pago > ?
        ORDER BY fecha_pago;
        """,
        (activo_id, fecha_ref.isoformat()),
    ).fetchall()
    if not pagos:
        return None

    flujos = [(fecha_ref, -precio_vn100)]
    flujos += [(date.fromisoformat(f), monto / divisor) for f, monto in pagos]
    return xirr(flujos)


def fx_implicito_usd_linked(con, activo_id: int, hoy: date) -> float | None:
    """Tipo de cambio (ARS por USD) con el que Rava paso a pesos el
    cronograma de un bono dollar-linked: amortizacion futura total en
    ARS / 100 (el capital residual es 100% del VN mientras no haya
    amortizado nada). Verificado 2026-10-14 con una ON dollar-linked: el
    cociente dio a tono con el mayorista del dia. Es el mismo
    tipo de cambio para todos los pagos del cronograma, que es lo que
    importa para la TIR; no depende de que USD_A3500 este cargado al dia.

    None si ya hubo alguna amortizacion (fecha_pago <= hoy): ahi el
    residual deja de ser 100 y la cuenta no vale (un bono que amortiza en
    cuotas) -- se marca con motivo en vez de dar un numero mal."""
    amortizado, futuro = con.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN fecha_pago <= ? THEN monto_por_vn END), 0),
               COALESCE(SUM(CASE WHEN fecha_pago > ? THEN monto_por_vn END), 0)
        FROM cronograma_pagos_bonos
        WHERE activo_id = ? AND tipo_pago = 'amortizacion';
        """,
        (hoy.isoformat(), hoy.isoformat(), activo_id),
    ).fetchone()
    if amortizado > 0 or futuro <= 0:
        return None
    return futuro / 100


REZAGO_CER_DIAS_HABILES = 10
TOLERANCIA_CER_DIAS = 5


def cer_rezagado(con, fecha: date) -> float | None:
    """CER que ajusta a un bono CER en `fecha`: por convencion de los
    bonos del Tesoro, el de 10 dias habiles antes. Los dias habiles se
    cuentan de lunes a viernes, sin feriados -- un feriado en la ventana
    corre el CER 1 dia (~0,07% con inflacion de ~2% mensual), y como la
    misma regla se aplica a precio y cronograma, el efecto en la TIR es
    menor todavia.

    Serie `cer` de macro_historico (BCRA variable 30, diaria incluidos
    fines de semana, ver cargar_indicadores_macro.py). None si no hay un
    dato a menos de TOLERANCIA_CER_DIAS de la fecha buscada -- no se
    estira un CER viejo."""
    objetivo, habiles = fecha, 0
    while habiles < REZAGO_CER_DIAS_HABILES:
        objetivo -= timedelta(days=1)
        if objetivo.weekday() < 5:
            habiles += 1
    fila = con.execute(
        "SELECT fecha, valor FROM macro_historico WHERE clave = 'cer' AND fecha <= ? ORDER BY fecha DESC LIMIT 1;",
        (objetivo.isoformat(),),
    ).fetchone()
    if fila is None or (objetivo - date.fromisoformat(fila[0])).days > TOLERANCIA_CER_DIAS:
        return None
    return fila[1]


def fecha_carga_cronograma(con, activo_id: int) -> date | None:
    """Dia en que se cargo el cronograma desde Rava (cargar_bonos_referencia_flujo
    borra y reinserta todo el cronograma del activo en cada carga, asi que
    todas sus filas comparten `actualizado_en`)."""
    fila = con.execute(
        "SELECT MAX(actualizado_en) FROM cronograma_pagos_bonos WHERE activo_id = ?;", (activo_id,)
    ).fetchone()
    return date.fromisoformat(fila[0][:10]) if fila and fila[0] else None


def cupon_anual_vn100(con, activo_id: int, fecha_ref: date) -> float:
    """Suma de pagos de INTERES (excluye amortizacion) por VN100 en los
    365 dias siguientes a `fecha_ref` -- "cupon anual" para Carry
    (running yield), Epica 11 (2026-09-11). Ya viene en
    `moneda_pago` del bono (misma convencion que `monto_por_vn` en
    general, ver tir_al).

    No prorratea ni asume periodicidad: un bono que vence dentro del
    año (LECAP/BONCAP capitalizados, que pagan TODO el interes
    acumulado en un unico pago al vencimiento) va a mostrar ese pago
    completo como "cupon anual" -- economicamente correcto (es
    realmente el retorno de interes de lo que le queda de vida), pero
    no es un cupon periodico tradicional. Se documenta, no se corrige:
    el pago real es el real."""
    row = con.execute(
        """
        SELECT COALESCE(SUM(monto_por_vn), 0)
        FROM cronograma_pagos_bonos
        WHERE activo_id = ? AND tipo_pago = 'interes'
          AND fecha_pago > ? AND fecha_pago <= ?;
        """,
        (activo_id, fecha_ref.isoformat(), (fecha_ref + timedelta(days=365)).isoformat()),
    ).fetchone()
    return row[0]


def entrada_ponderada(con, activo_id: int, moneda_pago: str) -> dict | None:
    """Fecha y precio de entrada (por VN100, en `moneda_pago`), ponderados
    por cantidad, sobre los CONTENEDORES donde la posicion neta hoy es
    > 0 (excluye contenedores donde compra+venta ya cerraron en 0 --
    ej. un "vehiculo" de conversion CCL/MEP que nunca quedo en cartera).

    Cada lote se lleva a `moneda_pago` ANTES de promediar: un bono duro
    (moneda_pago='USD') a veces se compra pagando en pesos
    (`movimientos.moneda='ARS'`) -- se convierte con el MEP HISTORICO de
    la fecha de esa compra (`dashboard.precio_fx_historico`), mismo
    criterio que ya usa `calcular_ganancia_por_activo` para "invertido".
    Si falta el MEP historico de esa fecha exacta, el lote completo
    queda fuera de la ponderacion (`verificable=False`) -- no se
    aproxima con el MEP de otro dia.

    `verificable=False` tambien si en algun contenedor incluido la
    cantidad bruta comprada no coincide con la posicion neta actual
    (senal de una venta PARCIAL de una posicion que sigue abierta -- no
    contemplado en este calculo v1, se marca en vez de asumir)."""
    contenedores_netos = con.execute(
        """
        SELECT contenedor_id, cantidad
        FROM vista_posiciones
        WHERE activo_id = ? AND cantidad > 1e-6;
        """,
        (activo_id,),
    ).fetchall()
    if not contenedores_netos:
        return None

    verificable = True
    total_monto, total_cantidad, fecha_ponderada_acum = 0.0, 0.0, 0.0
    cantidad_neta_total = 0.0

    for contenedor_id, cantidad_neta in contenedores_netos:
        cantidad_neta_total += cantidad_neta
        compras = con.execute(
            """
            SELECT m.fecha, m.monto, m.moneda, oa.cantidad
            FROM operaciones_activos oa
            JOIN movimientos m ON m.id = oa.movimiento_id
            WHERE oa.activo_id = ? AND oa.contenedor_id = ? AND m.tipo = 'compra_activo'
            ORDER BY m.fecha;
            """,
            (activo_id, contenedor_id),
        ).fetchall()
        cantidad_bruta = sum(c[3] for c in compras)
        if abs(cantidad_bruta - cantidad_neta) > 1e-6:
            verificable = False  # hubo venta parcial de una posicion que sigue abierta

        for fecha_str, monto, moneda_compra, cantidad in compras:
            monto_ajustado = _monto_en_moneda(con, monto, moneda_compra, moneda_pago, fecha_str)
            if monto_ajustado is None:
                verificable = False
                continue

            fecha_ordinal = date.fromisoformat(fecha_str).toordinal()
            total_monto += monto_ajustado
            total_cantidad += cantidad
            fecha_ponderada_acum += fecha_ordinal * cantidad

    if total_cantidad <= 0:
        return None

    precio_vn100 = total_monto / total_cantidad * 100
    fecha_ponderada = date.fromordinal(round(fecha_ponderada_acum / total_cantidad))
    return {
        "fecha": fecha_ponderada,
        "precio_vn100": precio_vn100,
        # `cantidad` es la posicion NETA actual (no la bruta comprada): tras una
        # venta parcial la bruta sobrestima tenencia,
        # valorizacion, carry y BPV. El precio/fecha de entrada siguen siendo
        # promedio ponderado de las compras (costo promedio, no cambia con
        # una venta parcial), por eso `total_cantidad` sigue pesando ese promedio.
        "cantidad": cantidad_neta_total,
        "verificable": verificable,
    }


def _monto_en_moneda(con, monto: float, moneda_compra: str, moneda_destino: str, fecha_str: str) -> float | None:
    """`monto` llevado a `moneda_destino` con el MEP HISTORICO de `fecha_str`
    (USD_CCL cuenta como USD). None si falta el MEP de esa fecha exacta --
    no se aproxima con el de otro dia."""
    import dashboard  # import tardio: evita import circular (dashboard no importa este modulo)

    if moneda_compra == moneda_destino or (moneda_compra == "USD_CCL" and moneda_destino == "USD"):
        return monto
    mep_historico = dashboard.precio_fx_historico(con, "USD_MEP", fecha_str)
    if mep_historico is None:
        return None
    return monto / mep_historico if moneda_destino == "USD" else monto * mep_historico


# Bonos que entraron a la cartera por canje (transferencia con monto 0) y
# cuyo costo real esta en las compras del bono ORIGINAL de la cadena.
# Ej. una ON reestructurada dos veces: lo unico que se pago fueron las
# compras del bono original.
# Decision del usuario (2026-10-14): no hay dato de a que TIR se compro, se
# toma de forma representativa "cuanto invertí vs. lo que me queda por
# cobrar" -- ver entrada_por_canje.
COSTO_ORIGEN_CANJE = {
    k: tuple(v) for k, v in configuracion.get("bonos", "costo_origen_canje", default={}).items()
}


def entrada_por_canje(con, activo_id: int, tickers_origen: tuple[str, ...], moneda_destino: str) -> dict | None:
    """Entrada REPRESENTATIVA de un bono recibido por canje: precio por
    VN100 = costo total de las compras de `tickers_origen` (en
    `moneda_destino`, MEP historico de cada compra) / posicion neta
    actual; fecha = la del ingreso por canje (primera fila del bono en
    operaciones_activos), desde donde corre el cronograma del bono nuevo.

    Aproximacion consciente, no un costo exacto: no descuenta lo cobrado
    entre la compra original y el canje ni prorratea si la cadena tuvo
    ventas parciales. `verificable=False` si falta el MEP de alguna compra."""
    cantidad_neta = con.execute(
        "SELECT COALESCE(SUM(cantidad), 0) FROM vista_posiciones WHERE activo_id = ? AND cantidad > 1e-6;",
        (activo_id,),
    ).fetchone()[0]
    fecha_canje = con.execute(
        """
        SELECT MIN(m.fecha) FROM operaciones_activos oa
        JOIN movimientos m ON m.id = oa.movimiento_id
        WHERE oa.activo_id = ?;
        """,
        (activo_id,),
    ).fetchone()[0]
    if cantidad_neta <= 0 or fecha_canje is None:
        return None

    compras = con.execute(
        f"""
        SELECT m.fecha, m.monto, m.moneda
        FROM operaciones_activos oa
        JOIN movimientos m ON m.id = oa.movimiento_id
        JOIN activos a ON a.id = oa.activo_id
        WHERE a.ticker IN ({",".join("?" * len(tickers_origen))}) AND m.tipo = 'compra_activo';
        """,
        tickers_origen,
    ).fetchall()
    if not compras:
        return None

    verificable, costo_total = True, 0.0
    for fecha_str, monto, moneda_compra in compras:
        monto_ajustado = _monto_en_moneda(con, abs(monto), moneda_compra, moneda_destino, fecha_str)
        if monto_ajustado is None:
            verificable = False
            continue
        costo_total += monto_ajustado

    return {
        "fecha": date.fromisoformat(fecha_canje),
        "precio_vn100": costo_total / cantidad_neta * 100,
        "cantidad": cantidad_neta,
        "costo_total": costo_total,
        "verificable": verificable,
    }


DURATION_MINIMA_CANDIDATO = 0.5  # años (~6 meses)
TOLERANCIA_CONSISTENCIA_PRECIO = 0.05  # 5% -- ver docstring


def candidatos_reemplazo(con, legislacion: str | None, duration_objetivo: float | None,
                          excluir_ticker: str, top_n: int = 3, ventana_duration: float = 2.0) -> list[dict]:
    """Bonos del mercado (mercado_renta_fija_rava, TIR/duration/paridad
    de Rava -- ver create_schema.py) con legislacion igual y duration
    dentro de +/- `ventana_duration` años, ordenados por TIR
    descendente. Devuelve datos crudos para que el usuario investigue y
    decida -- NO filtra por "riesgo de rotacion" (eso es su analisis:
    por que vender, por que comprar, si vale la pena -- decision
    explicita 2026-09-07).

    3 filtros de sanidad sobre el DATO en si (no sobre el merito de la
    inversion):
      - Consistencia precio vs. paridad*valor_tecnico (dentro de
        TOLERANCIA_CONSISTENCIA_PRECIO): encontrado 2026-09-07 que
        TLCJO/YMCPO/CH24D tienen `precio` ~1.521-1.522x mas grande que
        `paridad*valor_tecnico` (deberian ser el mismo numero) -- bug
        real en el feed de Rava para esos tickers puntuales, no
        distress. Como el `tir` de Rava se calcula a partir de ese
        `precio` corrupto, salia en 100-386% -- se descartan del
        universo ENTERO (no es que el bono sea "riesgoso", es que el
        dato no es confiable).
      - `duration >= DURATION_MINIMA_CANDIDATO`: un bono a semanas del
        vencimiento anualiza cualquier diferencia de precio chica en una
        TIR de cientos de % -- matematicamente correcto, sin sentido
        como comparacion (SNSBD/SNSBO a 40 dias, TIR ~966%/767%).
      - ventana proporcional al objetivo (max(ventana_duration, 50% del
        objetivo)): para un objetivo de duration chica, una ventana fija
        de +/-2 años dejaria pasar candidatos de duration mucho mas
        corta que igual no son comparables.

    `moneda_pago` NO esta verificada para este universo (a diferencia de
    los tickers en cartera) -- se devuelve tal cual viene de Rava, la
    decision final de si la moneda calza queda en manos del usuario."""
    if legislacion is None or duration_objetivo is None:
        return []
    ventana_efectiva = max(ventana_duration, duration_objetivo * 0.5)
    filas = con.execute(
        """
        SELECT ticker, nombre, tir, duration, paridad, vencimiento
        FROM mercado_renta_fija_rava
        WHERE legislacion = ? AND ticker != ?
          AND duration >= ?
          AND ABS(duration - ?) <= ?
          AND paridad IS NOT NULL AND valor_tecnico IS NOT NULL AND precio IS NOT NULL
          AND ABS(precio - paridad * valor_tecnico) <= ? * ABS(paridad * valor_tecnico)
        ORDER BY tir DESC
        LIMIT ?;
        """,
        (legislacion, excluir_ticker, DURATION_MINIMA_CANDIDATO, duration_objetivo, ventana_efectiva,
         TOLERANCIA_CONSISTENCIA_PRECIO, top_n),
    ).fetchall()
    return [
        {"ticker": t, "nombre": n, "tir_pct": round(tir * 100, 2), "duration": round(d, 2),
         "paridad_pct": round(p * 100, 2) if p is not None else None, "vencimiento": v}
        for t, n, tir, d, p, v in filas
    ]


def rotacion_bonos(con, mep: float, ccl: float, umbral_compresion_pp: float = 3.0) -> list[dict]:
    """Herramienta 1 (Epica 11): TIR de entrada vs. TIR de hoy por
    ticker de renta fija vigente, con candidatos de reemplazo (mismo
    universo de mercado, ver candidatos_reemplazo). Cada fila trae el
    motivo cuando no se puede calcular algo (moneda USD_LINKED sin
    metodo propio todavia, sin cronograma futuro, posicion no
    verificable) -- nunca se inventa un numero.

    `candidato_a_rotar=True` si la compresion (entrada - hoy, en pp)
    supera `umbral_compresion_pp` -- punto de partida sugerido por
    el usuario, ajustable por el llamador.

    `categoria`/`categoria_label` (2026-09-11): mismo clasificador que
    "Mercado de bonos" (clasificar_bonos), insumo del filtro por
    categoria del panel "Bonos en tenencia".

    `bpv_usd` (DV01), `carry_pct`/`carry_usd` (running yield) y
    `breakeven_pb` (cuantos pb de suba de tasa antes de que la perdida
    de precio le gane al carry del año) -- ver los comentarios en el
    cuerpo de la funcion para el detalle de cada formula."""
    tickers = con.execute(
        f"""
        SELECT DISTINCT a.id, a.ticker
        FROM vista_posiciones vp
        JOIN activos a ON a.id = vp.activo_id
        WHERE a.tipo IN ({",".join("?" * len(TIPOS_RENTA_FIJA))})
        ORDER BY a.ticker;
        """,
        TIPOS_RENTA_FIJA,
    ).fetchall()

    resultado = []
    for activo_id, ticker in tickers:
        fila: dict = {"ticker": ticker}

        # Emisor / paridad / duration del snapshot de mercado (2026-09-10:
        # la tabla de la solapa dejo de ser "rotacion" y paso a ser
        # "Bonos en tenencia" -- muestra estos junto a PPC/invertido).
        mkt = con.execute(
            "SELECT emisor, paridad_calc, duration, tipo_instrumento, legislacion, dm "
            "FROM mercado_renta_fija_rava WHERE ticker = ?;",
            (ticker,),
        ).fetchone()
        fila["emisor"] = mkt[0] if mkt else None
        fila["paridad_pct"] = round(mkt[1] * 100, 2) if mkt and mkt[1] is not None else None
        duration_propia = mkt[2] if mkt else None
        dm_propio = mkt[5] if mkt else None
        # Duration (Macaulay, en años, de Rava) -- pedido del usuario
        # 2026-09-16 para verla directo en "Bonos en tenencia" (antes solo
        # se usaba puertas adentro, para el BPV y para buscar candidatos de
        # reemplazo de duration parecida). Seteada aca, ANTES de cualquier
        # 'continue' de mas abajo, para que aparezca aun en filas que
        # cortan temprano (sin bonos_referencia, sin precio, etc.) -- mismo
        # criterio que emisor/paridad_pct/categoria.
        fila["duration"] = round(duration_propia, 2) if duration_propia is not None else None

        # Categoria (2026-09-11, mismo clasificador que "Mercado de bonos"
        # -- clasificar() ya resuelve por CATEGORIA_OVERRIDE de ticker aun
        # sin fila en mercado_renta_fija_rava).
        tipo_instrumento_mkt = mkt[3] if mkt else None
        legislacion_mkt = mkt[4] if mkt else None
        fila["categoria"] = clasificar_bonos.clasificar(ticker, tipo_instrumento_mkt, legislacion_mkt)
        fila["categoria_label"] = clasificar_bonos.CATEGORIA_LABEL[fila["categoria"]]

        ref = con.execute(
            "SELECT tipo_instrumento, legislacion, moneda_pago FROM bonos_referencia WHERE activo_id = ?;",
            (activo_id,),
        ).fetchone()
        if ref is None:
            fila["motivo"] = "sin bonos_referencia cargado"
            resultado.append(fila)
            continue
        _tipo_instrumento, legislacion, moneda_cronograma = ref

        # USD_LINKED (2026-10-14): todo el calculo va en USD; el
        # cronograma (en ARS) se divide por el tipo de cambio implicito.
        # De aca en mas `moneda_pago` es la moneda del CALCULO (USD/ARS).
        fx_cronograma = 1.0
        if moneda_cronograma == "USD_LINKED":
            fx_cronograma = fx_implicito_usd_linked(con, activo_id, configuracion.hoy())
            if fx_cronograma is None:
                fila["motivo"] = "USD_LINKED ya con amortizaciones pagadas: el tipo de cambio implicito no aplica (ver fx_implicito_usd_linked)"
                resultado.append(fila)
                continue
            moneda_pago = "USD"
        elif moneda_cronograma in ("USD", "ARS"):
            moneda_pago = moneda_cronograma
        else:
            fila["motivo"] = f"moneda_pago={moneda_cronograma!r} sin metodo de TIR todavia (ver docstring)"
            resultado.append(fila)
            continue

        tipo_activo = _tipo_o_generico(con, activo_id)

        if ticker in COSTO_ORIGEN_CANJE:
            entrada = entrada_por_canje(con, activo_id, COSTO_ORIGEN_CANJE[ticker], moneda_pago)
            if entrada is not None:
                fila["entrada_por_canje"] = True
                # Lo invertido de verdad (las compras del bono original):
                # el panel de Ganancia no lo tiene porque el canje entro con monto 0.
                fila["invertido_usd"] = round(entrada["costo_total"], 2)
        else:
            entrada = entrada_ponderada(con, activo_id, moneda_pago)
        if entrada is None:
            fila["motivo"] = "sin compras vigentes (no deberia pasar, posicion viene de vista_posiciones)"
            resultado.append(fila)
            continue

        precio_actual_row = precio_actual(con, activo_id)  # en vivo en la rueda (ADR-033)
        if precio_actual_row is None:
            fila["motivo"] = "sin precio actual cargado"
            resultado.append(fila)
            continue
        precio_hoy_nativo, moneda_precio = precio_actual_row

        # El precio GUARDADO puede venir en una moneda distinta de la
        # moneda REAL de pago del bono (ej. la mayoria de los bonos duros
        # se guardan en ARS -- especie O -- y se convierten a USD por MEP
        # recien en valorizacion.py, ver ESTADO_PWOS.md 2026-07-14). Se
        # convierte a moneda_pago con el MISMO criterio que el resto del
        # dashboard (convertir_a_usd, MEP -- ningun bono esta en
        # TIPOS_CCL) en vez de bloquear por "no coincide".
        if moneda_precio == moneda_pago:
            precio_hoy = precio_hoy_nativo
        elif moneda_pago == "USD":
            precio_hoy = convertir_a_usd(tipo_activo, precio_hoy_nativo, moneda_precio, mep, ccl)
        else:
            fila["motivo"] = f"precio en {moneda_precio}, no hay conversion definida hacia moneda_pago={moneda_pago}"
            resultado.append(fila)
            continue

        # Bonos CER: todo a unidades reales (ver docstring del modulo).
        # Cronograma / CER del dia de carga; precio / CER de su fecha.
        # La entrada usa la fecha ponderada (aproximacion si hubo varias
        # compras).
        if tipo_activo == "bono_cer":
            fecha_carga = fecha_carga_cronograma(con, activo_id)
            cer_carga = cer_rezagado(con, fecha_carga) if fecha_carga else None
            cer_entrada = cer_rezagado(con, entrada["fecha"])
            cer_hoy = cer_rezagado(con, configuracion.hoy())
            if None in (cer_carga, cer_entrada, cer_hoy):
                fila["motivo"] = "falta el CER de alguna fecha (cargar_indicadores_macro.py --indicador cer)"
                resultado.append(fila)
                continue
            fila["tir_real"] = True
            tir_entrada = tir_al(con, activo_id, entrada["fecha"], entrada["precio_vn100"] / cer_entrada, cer_carga)
            tir_hoy = tir_al(con, activo_id, configuracion.hoy(), precio_hoy / cer_hoy, cer_carga)
            # Cupon del cronograma (al CER de la carga) llevado al CER de hoy,
            # para que el Carry % compare contra el precio de hoy en la misma escala.
            ajuste_cupon_cer = cer_hoy / cer_carga
        else:
            tir_entrada = tir_al(con, activo_id, entrada["fecha"], entrada["precio_vn100"], fx_cronograma)
            tir_hoy = tir_al(con, activo_id, configuracion.hoy(), precio_hoy, fx_cronograma)
            ajuste_cupon_cer = 1.0

        fila.update({
            "cantidad": entrada["cantidad"],
            "fecha_entrada_ponderada": entrada["fecha"].isoformat(),
            "precio_entrada_vn100": round(entrada["precio_vn100"], 4),
            "precio_hoy_vn100": round(precio_hoy, 4),
            "moneda": moneda_pago,
            "entrada_verificable": entrada["verificable"],
            "tir_entrada_pct": round(tir_entrada * 100, 2) if tir_entrada is not None else None,
            "tir_hoy_pct": round(tir_hoy * 100, 2) if tir_hoy is not None else None,
        })

        if tir_entrada is None or tir_hoy is None:
            fila["motivo"] = "xirr no convergio o sin cronograma futuro (bono muy cerca del vencimiento)"
            resultado.append(fila)
            continue

        compresion_pp = (tir_entrada - tir_hoy) * 100
        fila["compresion_pp"] = round(compresion_pp, 2)
        fila["candidato_a_rotar"] = compresion_pp > umbral_compresion_pp

        # `precio_hoy` ya esta en `moneda_pago` (conversion hecha arriba) --
        # NO volver a pasar por convertir_a_usd con `moneda_precio`
        # original, eso convertiria dos veces. Bonos nunca estan en
        # TIPOS_CCL (valorizacion.py) asi que la unica conversion posible
        # de aca en mas es ARS/MEP, directa.
        valor_moneda_pago = valor_posicion_nativo(tipo_activo, entrada["cantidad"], precio_hoy)
        fila["valorizacion_usd"] = round(valor_moneda_pago if moneda_pago == "USD" else valor_moneda_pago / mep, 2)

        # Carry / running yield (2026-09-11): cupon
        # anual (por VN100, ver cupon_anual_vn100) sobre precio actual --
        # ambos en `moneda_pago`, la conversion no hace falta para el %.
        # `carry_usd` (posicion completa, mismo patron VN/100 x monto que
        # valorizacion) se usa para agregar a nivel cartera/categoria y
        # para Breakeven (no se expone como columna propia, "Carry %" ya
        # es la lectura por bono que pidio el usuario).
        cupon_vn100 = cupon_anual_vn100(con, activo_id, configuracion.hoy()) / fx_cronograma * ajuste_cupon_cer
        fila["carry_pct"] = round(cupon_vn100 / precio_hoy * 100, 2) if precio_hoy else None
        carry_moneda_pago = valor_posicion_nativo(tipo_activo, entrada["cantidad"], cupon_vn100)
        carry_usd = carry_moneda_pago if moneda_pago == "USD" else carry_moneda_pago / mep
        fila["carry_usd"] = round(carry_usd, 2)

        # BPV / DV01 (2026-09-11): sensibilidad de precio
        # a +1pb de suba de tasas -- BPV = Precio x Mod.Duration x 0.0001
        # x (VN/100), que es exactamente valor_moneda_pago (= Precio x
        # VN/100, ya calculado arriba) x dm x 0.0001 -- se reusa en vez de
        # recalcular. Magnitud positiva (convencion estandar de DV01, no
        # se le mete signo aca); la lectura "-$X por +1pb" es un criterio
        # de presentacion del front, no del dato. Requiere `dm` (modified
        # duration) de Rava -- no todas las especies lo traen; si falta
        # queda None.
        #
        # Breakeven (pb, "breakeven yield"): cuantos pb puede subir la TIR
        # de ESTE bono antes de que la perdida de precio (BPV x pb) le
        # gane al carry cobrado en el año -- Breakeven = carry_usd/bpv_usd.
        # Se divide con el bpv SIN redondear (no el fila["bpv_usd"] ya
        # redondeado a 2 decimales) para no arrastrar el redondeo.
        if dm_propio is not None:
            bpv_moneda_pago = valor_moneda_pago * dm_propio * 0.0001
            bpv_usd = bpv_moneda_pago if moneda_pago == "USD" else bpv_moneda_pago / mep
            fila["bpv_usd"] = round(bpv_usd, 2)
            fila["breakeven_pb"] = round(carry_usd / bpv_usd, 1) if bpv_usd else None
        else:
            fila["bpv_usd"] = None
            fila["breakeven_pb"] = None

        # `candidatos_reemplazo` sigue en la respuesta para el reporte CLI
        # (reporte_rotacion_bonos.py) -- el dashboard dejo de mostrarlo
        # (2026-09-10: la tabla es "Bonos en tenencia").
        fila["candidatos_reemplazo"] = candidatos_reemplazo(con, legislacion, duration_propia, ticker)

        resultado.append(fila)

    return resultado


def sensibilidad_tasa_cartera(con, mep: float, ccl: float) -> dict:
    """Sensibilidad de tasa agregada de la cartera de renta fija -- BPV,
    Carry y Breakeven, total + por categoria (2026-09-11, pedido del
    usuario: "que categoria concentra mas riesgo de tasa", y cuanto
    breakeven de carry hay contra ese riesgo). BPV y Carry son aditivos
    (a diferencia de duration/TIR, sumar alcanza, sin ponderar); Carry
    % y Breakeven son RATIOS, se recalculan sobre los totales ya sumados
    (nunca promediando los % de cada bono). Solo suma posiciones con
    `bpv_usd` calculable (ver rotacion_bonos para el motivo de cada
    exclusion) -- `tickers_sin_calcular` deja rastro de lo que quedo
    afuera en vez de esconderlo."""
    filas = rotacion_bonos(con, mep, ccl)
    calculables = [f for f in filas if f.get("bpv_usd") is not None]
    bpv_total = sum(f["bpv_usd"] for f in calculables)
    carry_total = sum(f["carry_usd"] for f in calculables)
    valor_total = sum(f["valorizacion_usd"] for f in calculables)

    por_cat: dict[str, dict] = {}
    for f in calculables:
        c = por_cat.setdefault(f["categoria"], {"bpv_usd": 0.0, "carry_usd": 0.0, "valor_usd": 0.0})
        c["bpv_usd"] += f["bpv_usd"]
        c["carry_usd"] += f["carry_usd"]
        c["valor_usd"] += f["valorizacion_usd"]
    categorias = [
        {
            "categoria": clave,
            "categoria_label": clasificar_bonos.CATEGORIA_LABEL[clave],
            "bpv_usd": round(c["bpv_usd"], 2),
            "carry_usd": round(c["carry_usd"], 2),
            "carry_pct": round(c["carry_usd"] / c["valor_usd"] * 100, 2) if c["valor_usd"] else None,
        }
        for clave, c in por_cat.items()
    ]
    categorias.sort(key=lambda x: x["bpv_usd"], reverse=True)

    return {
        "bpv_total_usd": round(bpv_total, 2),
        "carry_total_usd": round(carry_total, 2),
        "carry_pct_total": round(carry_total / valor_total * 100, 2) if valor_total else None,
        "breakeven_total_pb": round(carry_total / bpv_total, 1) if bpv_total else None,
        "tickers_sin_calcular": [f["ticker"] for f in filas if f.get("bpv_usd") is None],
        "categorias": categorias,
    }


def _tipo_o_generico(con, activo_id: int) -> str:
    return con.execute("SELECT tipo FROM activos WHERE id = ?;", (activo_id,)).fetchone()[0]
