"""
PWOS — Tablero de control (versión terminal, provisoria)

Responde la Pregunta 1: ¿cuánto tengo? — todo valorizado en USD MEP.
Esto es un prototipo de la capa de PRESENTACIÓN. Más adelante esta
misma lógica va a alimentar al bot de Telegram (ADR-005); por ahora
la vemos impresa acá para poder validar que los cálculos tienen sentido.
"""
import sqlite3
from datetime import date
from pathlib import Path

import configuracion
from valorizacion import valor_posicion_nativo, convertir_a_usd, convertir_efectivo_a_usd, pct_de, pct_propiedad_por_cuenta, TIPOS_CCL, precio_actual, fx_actual
from riesgo import (
    calcular_score_renta_fija, calcular_score_equity, anios_a_vencimiento,
    RATING_EMISOR_SOBERANO, TICKER_EMISOR_SOBERANO, TICKER_TIPO_INSTRUMENTO_SOBERANO,
)

DB_PATH = Path(__file__).parent / "pwos.db"

# Orden de lectura para la seccion INVERSIONES: renta fija primero (mas
# peso en la cartera hoy), despues equity. Cualquier tipo no listado
# cae al final, ordenado alfabeticamente.
ORDEN_TIPOS = [
    "bono_soberano", "bono_provincial", "bono_cer", "on", "lecap", "fci",
    "accion", "cedear",
]


def obtener_precio_fx(con, ticker: str) -> float:
    """Tipo de cambio para valuar hoy: en vivo durante la rueda, si no el
    ultimo cierre (ADR-033, `valorizacion.fx_actual`)."""
    return fx_actual(con, ticker)


def obtener_benchmarks_macro(con) -> dict:
    """3 referencias de benchmark del header (2026-08-29, ver
    cargar_benchmarks_macro.py) -- inflación Argentina interanual,
    devaluación del peso interanual (USD MEP) y S&P 500 interanual.
    Lee el último valor cacheado en `benchmarks_macro` (1 fila por
    clave, UPSERT) -- nunca pega contra las fuentes externas en cada
    carga del dashboard, mismo criterio que factores_riesgo. Una clave
    sin fila todavía (nunca se corrió el script, o falló esa fuente en
    la última corrida) queda en `None` -- no se inventa un valor."""
    filas = {
        clave: {"valor_pct": valor_pct, "fecha_dato": fecha_dato}
        for clave, valor_pct, fecha_dato in con.execute(
            "SELECT clave, valor_pct, fecha_dato FROM benchmarks_macro;"
        )
    }
    claves = ["inflacion_ars_interanual", "devaluacion_ars_interanual", "sp500_interanual"]
    return {clave: filas.get(clave) for clave in claves}


def valorizar_saldos_cuenta(con, mep: float) -> list[dict]:
    """Efectivo de cada cuenta en USD, ya en la parte propia (ADR-032:
    `pct_propiedad` del contenedor, de valorizacion.pct_propiedad_por_cuenta).
    `pct_propiedad` < 100 = contenedor compartido: el concepto lo aclara."""
    pcts = pct_propiedad_por_cuenta(con)
    filas = []
    for cuenta_id, alias, moneda, estado, contenedor, saldo in con.execute(
        "SELECT * FROM vista_saldos_cuenta;"
    ):
        if moneda == "CARTERA":
            continue  # no es efectivo real, se valoriza vía posiciones
        pct = pct_de(pcts, cuenta_id)
        valor_usd = convertir_efectivo_a_usd(saldo, moneda, mep, pct)
        concepto = f"{contenedor} · {alias} ({estado})"
        if pct < 100:
            concepto += f" — {pct:g}% del saldo real"
        filas.append({
            "concepto": concepto,
            "contenedor": contenedor,
            "valor_usd": valor_usd,
            "pct_propiedad": pct,
        })
    return filas


# Slots de cuenta de efectivo en orden fijo, para el cruce contra la app
# de cada broker (mismo criterio y orden que reporte_saldos_efectivo.py).
# USD_CCL (dolar cable, ADR-011) va como columna propia: es un dolar pero
# no fungible 1:1 con el USD/MEP general y en la app del broker figura
# aparte.
SLOTS_EFECTIVO = [
    ("disponible", "ARS"),
    ("disponible", "USD"),
    ("caucionado", "ARS"),
    ("caucionado", "USD"),
    ("plazo_fijo", "ARS"),
    ("plazo_fijo", "USD"),
    ("disponible", "USD_CCL"),
]


def saldos_nativos_por_contenedor(con) -> list[dict]:
    """Saldo de efectivo en moneda NATIVA (sin convertir a USD) por
    contenedor y slot de cuenta, para que el usuario lo cruce renglon por
    renglon contra lo que ve en cada app de broker. Solo lectura, misma
    fuente que el dashboard (`vista_saldos_cuenta`). Un slot que ese
    contenedor no tiene queda en None (no 0) -- para distinguir 'cuenta
    en 0' de 'cuenta que no existe'.

    Siempre al 100% (el saldo de la app), aunque el contenedor sea
    compartido (ADR-032): `pct_propiedad` viaja solo para aclararlo."""
    pcts = pct_propiedad_por_cuenta(con)
    por_contenedor: dict[str, dict] = {}
    pct_contenedor: dict[str, float] = {}
    for cuenta_id, _alias, moneda, estado, contenedor, saldo in con.execute(
        "SELECT * FROM vista_saldos_cuenta;"
    ):
        if moneda == "CARTERA":
            continue
        if (estado, moneda) not in SLOTS_EFECTIVO:
            continue
        por_contenedor.setdefault(contenedor, {})[(estado, moneda)] = saldo
        pct_contenedor[contenedor] = pct_de(pcts, cuenta_id)

    filas = []
    for contenedor, slots in sorted(por_contenedor.items()):
        # ADR-011 siembra una cuenta USD_CCL en 0 para TODO contenedor
        # broker, aunque nunca se haya operado -- si eso es lo unico que
        # tiene, el contenedor no esta realmente en uso: se omite para no
        # llenar la tabla de filas vacias (contenedores sin datos).
        tiene_cuenta_real = any(
            slots.get((est, mon)) is not None
            for est, mon in SLOTS_EFECTIVO
            if mon != "USD_CCL"
        )
        if not tiene_cuenta_real:
            continue
        filas.append({
            "contenedor": contenedor,
            "pct_propiedad": pct_contenedor.get(contenedor, 100.0),
            "slots": [
                {
                    "estado": estado,
                    "moneda": moneda,
                    "saldo": round(s, 2) if (s := slots.get((estado, moneda))) is not None else None,
                }
                for estado, moneda in SLOTS_EFECTIVO
            ],
        })
    return filas


def valorizar_posiciones(con, mep: float, ccl: float) -> list[dict]:
    filas = []
    for activo_id, ticker, tipo_activo, contenedor_id, contenedor, cantidad in con.execute(
        "SELECT * FROM vista_posiciones;"
    ):
        precio_row = precio_actual(con, activo_id)
        if precio_row is None:
            valor_usd = None  # sin precio cargado, no se puede valorizar
        else:
            precio, moneda_precio = precio_row
            valor_nativo = valor_posicion_nativo(tipo_activo, cantidad, precio)
            valor_usd = convertir_a_usd(tipo_activo, valor_nativo, moneda_precio, mep, ccl)
        filas.append({
            "tipo": tipo_activo,
            "ticker": ticker,
            "contenedor": contenedor,
            "cantidad": cantidad,
            "valor_usd": valor_usd,
        })
    return filas


# Dividendos en especie donde el usuario confirmo el origen (2026-08-20):
# hay un valor economico real recibido (no plata, pero si nominales de
# un instrumento que cotiza), a diferencia de un canje/ajuste generico
# (ej. un canje de ON) donde no hay una fecha de "recepcion de valor" limpia.
# Costo = precio de mercado del instrumento en la fecha de recepcion x
# cantidad recibida, convertido al tipo de cambio de esa misma fecha --
# mismo criterio que una compra real en ARS, solo que el "monto pagado"
# es un valor de mercado en vez de una transaccion. Backfill de estos
# precios puntuales en backfill_dividendos_especie_2026-08-20.py.
DIVIDENDOS_EN_ESPECIE_CONFIRMADOS = set(configuracion.get("renta", "dividendos_en_especie_confirmados", default=[]))


def precio_mercado_historico(con, activo_id: int, fecha: str) -> tuple[float, str] | None:
    """(precio, moneda) de un activo cualquiera (no solo FX) a una fecha
    puntual, desde `precios`. None si no hay dato para esa fecha exacta."""
    row = con.execute(
        "SELECT precio, moneda FROM precios WHERE activo_id = ? AND fecha = ?;", (activo_id, fecha)
    ).fetchone()
    return (row[0], row[1]) if row else None


def precio_fx_historico(con, moneda_fx_ticker: str, fecha: str) -> float | None:
    """Cotizacion de venta de USD_MEP/USD_CCL a una fecha puntual (tabla
    `precios`, backfill de ArgentinaDatos -- ver
    backfill_fx_historico_ganancia_2026-08-20.py). None si no hay dato
    para esa fecha exacta -- no se aproxima con el dia mas cercano ni
    con el de hoy, el llamador decide que hacer con el hueco."""
    row = con.execute("""
        SELECT p.precio FROM precios p JOIN activos a ON a.id = p.activo_id
        WHERE a.ticker = ? AND p.fecha = ?;
    """, (moneda_fx_ticker, fecha)).fetchone()
    return row[0] if row else None


# Amortizacion ya cobrada -- devuelve parte del capital al inversor
# antes del vencimiento. Si "ganancia/perdida" solo compara "valor
# actual de lo que queda" contra "invertido original" sin sumar lo ya
# devuelto en efectivo, un bono que amortizo aparece en perdida aunque
# el retorno total sea neutro o positivo (encontrado 2026-08-21, caso
# real: un bono amortizable con -20% "de perdida" que en realidad es capital ya cobrado).
#
# No hay un campo unico en toda la base que distinga amortizacion de
# interes para los 5 brokers: `flujos_esperados.tipo_flujo` lo tiene,
# pero SOLO lo escribe el importador de un broker --
# los otros 4 brokers nunca escriben en flujos_esperados -- y ademas
# tiene huecos incluso para ese broker (movimientos incrementales recientes
# sin backfillear, ver ESTADO_PWOS.md). Por eso el criterio ES la
# descripcion real de `movimientos`, verificada por broker, no ese
# campo: patron de texto donde el broker distingue amortizacion de
# interes en el texto mismo (generaliza solo a pagos FUTUROS del mismo
# ticker en el mismo broker, sin tocar este codigo) + una lista chica
# de movimientos confirmados a mano para brokers sin ese patron (mismo
# criterio que DIVIDENDOS_EN_ESPECIE_CONFIRMADOS mas abajo).
PATRONES_AMORTIZACION_TEXTO = configuracion.get("renta", "patrones_amortizacion_texto", default=[])

# Un broker sin patron de texto propio: la
# descripcion es identica para interes y amortizacion ("RTA TICKER-...",
# ambos el mismo dia a veces) -- no se puede distinguir por texto.
# Se confirma a mano cruzando cronograma_pagos_bonos: la proporcion entre
# los 2 movimientos del mismo dia coincide con la proporcion
# amortizacion/interes del cronograma para esa fecha -- movimiento_id ->
# ticker, en la configuracion, no un monto (para que un
# pago futuro del mismo ticker en ese broker siga marcado "no
# verificable" hasta confirmarse a mano de la misma forma, en vez de
# adivinarse).
# movimiento_id -> ticker, en la configuracion del usuario.
AMORTIZACIONES_CONFIRMADAS_MANUAL = {
    int(k): v for k, v in configuracion.get("renta", "amortizaciones_confirmadas_manual", default={}).items()
}


def amortizaciones_cobradas(con, ticker: str) -> list[tuple]:
    """(fecha, monto, moneda) de cada pago de amortizacion YA COBRADO
    para este ticker -- nunca interes, ver PATRONES_AMORTIZACION_TEXTO /
    AMORTIZACIONES_CONFIRMADAS_MANUAL arriba para el criterio de
    clasificacion por broker."""
    filas = []
    for patron in PATRONES_AMORTIZACION_TEXTO:
        filas += con.execute(
            # "[Pesos] ..." (un broker): fila espejo de la retencion en ARS del
            # cobro real en USD, NO un segundo ingreso -- confirmado
            # 2026-08-21 con un bono (la retencion en ARS junto al cobro
            # real en USD el mismo dia; el propio importador ya la excluye de
            # `cantidad` por el mismo motivo, ver CANTIDAD_EXCLUIDA en
            # el dry run del broker). Sin este filtro se sumaba dos veces.
            "SELECT fecha, monto, moneda FROM movimientos "
            "WHERE tipo = 'renta' AND naturaleza = 'efectivo' AND descripcion LIKE ? "
            "AND descripcion NOT LIKE '[Pesos]%';",
            (patron.format(ticker=ticker),),
        ).fetchall()
    for movimiento_id, tk in AMORTIZACIONES_CONFIRMADAS_MANUAL.items():
        if tk == ticker:
            filas += con.execute(
                "SELECT fecha, monto, moneda FROM movimientos WHERE id = ?;", (movimiento_id,)
            ).fetchall()
    return filas


def calcular_ganancia_por_activo(con, mep: float, ccl: float) -> list[dict]:
    """Ganancia/pérdida NO realizada por instrumento, agregada entre
    brokers (mismo ticker en 2 brokers = 1 sola fila) -- panel del
    rediseño del dashboard (2026-08-19, revisado 2026-08-20). Fórmula
    fijada por el usuario, la misma que ya usa el cliente de la API del broker,
    generalizada acá a todos los brokers vía el ledger propio en vez de
    confiar en el gananciaDinero/gananciaPorcentaje que devuelve cada
    API (metodología distinta entre brokers, no comparable).

    CAMBIO 2026-08-20: "invertido" tiene que reflejar los dólares
    REALES puestos en cada compra, al tipo de cambio DE ESE DÍA -- no
    una reconversión del costo en pesos al dólar de HOY (eso mezclaría
    el rendimiento del activo con el movimiento del dólar, y ya no
    respondería "¿fue buena mi decisión de inversión?"). Por eso ahora
    se acumula un costo en USD por compra (no un `ppc` en moneda
    nativa que se reconvertía al final):

      - Compra en 'USD' o 'USD_CCL': el `monto` ya es dólares reales,
        se usa directo, sin conversión.
      - Compra en 'ARS': `monto` (la plata real pagada, ver criterio
        del fix anterior) se convierte al MEP o CCL de LA FECHA DE ESA
        COMPRA (`precio_fx_historico`) -- CCL para CEDEARs, MEP para
        el resto (mismo criterio ya vigente para "valor actual").
      - Compra `naturaleza='en_especie'` (canje, dividendo en especie,
        ajuste manual -- no hay plata real que convertir a ningún tipo
        de cambio real): NO se adivina. La posición completa se marca
        `"verificable": False` con el motivo, y se calcula igual que
        antes (ppc nativo × dólar de HOY) SOLO como referencia, para
        no hacerla desaparecer del panel.
      - Si falta el dato de FX histórico para la fecha de una compra en
        ARS (hueco real, no debería pasar hoy -- se verificó cobertura
        completa el 2026-08-20): mismo tratamiento, `"verificable":
        False`, no se aproxima con el dólar de otro día.

    Las ventas reducen el costo acumulado EN PROPORCIÓN a lo vendido
    (mismo método de costo promedio ponderado de siempre, ahora llevado
    en USD en vez de en moneda nativa) -- vender no cambia el costo
    promedio por unidad de lo que queda.

    "Valor actual" NO cambia: sigue siendo el valor de mercado de HOY
    convertido al tipo de cambio de HOY (`valor_posicion_nativo` +
    `convertir_a_usd`, misma fuente que ya usa la torta de composición)
    -- eso sí responde correctamente "¿cuánto vale esto ahora?".

    CAMBIO 2026-08-21: `ganancia_porcentaje` se calcula sobre el
    RETORNO TOTAL (`valor_actual_usd + amortizacion_cobrada_usd`), no
    solo sobre `valor_actual_usd` -- un bono que ya devolvió parte del
    capital (amortización parcial) achica el valor de la tenencia
    remanente sin que eso sea una pérdida real (esa plata ya está
    cobrada en efectivo, no desapareció): un bono con 3 amortizaciones
    ya cobradas mostraba -20% de "pérdida". El interés (cupón) queda deliberadamente AFUERA de este
    cálculo -- ya se muestra aparte en el panel de flujo de pagos, y
    sumarlo acá dejaría de responder "¿gané o perdí sobre el capital?"
    para pasar a incluir el rendimiento corriente. Ver
    `amortizaciones_cobradas` arriba para de dónde sale el dato (no hay
    un campo único que distinga amortización de interés para los 5
    brokers) y `ganancia_porcentaje` ahora se calcula DIRECTO de los USD
    (`retorno_total_usd / invertido_usd - 1`), no de un ratio en moneda
    nativa -- a propósito: ese ratio en moneda nativa ignoraba el
    movimiento del dólar entre la compra y hoy, que es exactamente lo
    que esta fórmula quiere capturar.

    CAMBIO 2026-08-26 -- ganancia REALIZADA por ventas, agregada en el
    mismo loop (a propósito, no una función paralela): cada `venta_activo`
    ya reducía `invertido_usd` en proporción a lo vendido para que el
    costo promedio de lo que queda no se mueva -- ese costo liberado
    (`costo_usd_por_nominal * cantidad`) antes se descartaba, ahora se
    acumula en `costo_vendido_usd` junto con lo efectivamente cobrado por
    esa venta en USD (`producido_usd`, mismo criterio que "invertido":
    moneda USD/USD_CCL directo, ARS al MEP/CCL histórico DE LA FECHA DE
    LA VENTA). Al ser el mismo acumulador que ya usa el lado no
    realizado, `costo_vendido_usd + invertido_usd remanente == invertido_usd
    original` por construcción -- no puede desalinearse.

    Ventas SIN venta de mercado real quedan afuera a propósito (mismo
    filtro `WHERE m.tipo IN (...)` de siempre, sin tocar): un bono que
    amortiza 100% al vencimiento cierra con `tipo='renta'`, un canje
    (ej. una ON reestructurada) cierra con `tipo='transferencia'` -- ninguno de
    los dos es una venta de mercado, y su "ganancia" (si aplica) ya se
    trata aparte (amortización = capital devuelto, no rendimiento; canje
    = sin plata real de por medio). Decisión del usuario, no un hueco.

    CAMBIO 2026-09-03: "posición abierta" se decide por el neto REAL del
    activo (`neto_real_por_activo`, que incluye los cierres vía
    renta/canje), no por el neteo de compra/venta -- antes una posición
    amortizada 100% al vencimiento seguía figurando abierta acá si
    todavía tenía un precio cargado (algunas LECAPs mostraban +130% de
    ganancia fantasma). Ahora sale del panel, consistente con
    `vista_posiciones` y con lo que dice el párrafo de arriba.

    Una posición que quedó en 0 (100% vendida) ahora SÍ aparece en el
    resultado si tuvo ventas -- antes se descartaba entera
    (`# posicion cerrada`) porque no había nada no-realizado que mostrar;
    ahora los campos de no-realizado quedan en `None` y los de realizado
    llevan el dato real (`posicion_abierta: False`)."""
    # v2.48: los splits/contrasplits (eventos_corporativos) entran como una
    # fila mas, ANTES de las operaciones del mismo dia (una compra ese dia ya
    # es en unidades nuevas). `cantidad` = cantidad_despues - cantidad_antes.
    filas_crudas = [fila[2:] for fila in con.execute("""
        SELECT 1 AS orden, m.id AS id, oa.activo_id, a.ticker, a.tipo, m.fecha, m.tipo, oa.cantidad,
               oa.precio_unitario, oa.moneda, m.monto, m.naturaleza
        FROM operaciones_activos oa
        JOIN movimientos m ON m.id = oa.movimiento_id
        JOIN activos a ON a.id = oa.activo_id
        WHERE m.tipo IN ('compra_activo', 'venta_activo')
        UNION ALL
        SELECT 0, e.id, e.activo_id, a.ticker, a.tipo, e.fecha, 'evento_corporativo',
               e.cantidad_despues - e.cantidad_antes, NULL, NULL, 0, 'en_especie'
        FROM eventos_corporativos e
        JOIN activos a ON a.id = e.activo_id
        ORDER BY 3, 6, 1, 2;
    """).fetchall()]

    # Neto REAL por activo, incluyendo cierres que NO son compra/venta:
    # un bono que amortiza 100% al vencimiento cierra con `tipo='renta'`
    # y un canje cierra con `tipo='transferencia'` -- ninguno aparece en
    # `filas_crudas` (filtrada a compra/venta), asi que una posicion
    # cerrada de esa forma quedaba figurando como abierta si todavia
    # tenia un precio cargado (2026-09-03: dos LECAPs vencidas con
    # serie de Rava hasta despues del vencimiento -> ganancia inventada
    # de +130%). Misma regla de signo que `vista_posiciones`. Verificado
    # (2026-09-03) que para toda posicion genuinamente abierta este neto
    # coincide exacto con el de compra/venta -- el cambio solo saca las
    # ya cerradas, no toca ninguna abierta.
    neto_real_por_activo = dict(con.execute("""
        SELECT activo_id, SUM(cantidad) FROM (
            SELECT oa.activo_id,
                   CASE WHEN m.tipo = 'compra_activo' THEN oa.cantidad ELSE -oa.cantidad END AS cantidad
            FROM operaciones_activos oa
            JOIN movimientos m ON m.id = oa.movimiento_id
            UNION ALL
            SELECT activo_id, cantidad_despues - cantidad_antes FROM eventos_corporativos
        )
        GROUP BY activo_id;
    """).fetchall())

    posiciones: dict[int, dict] = {}
    for activo_id, ticker, tipo, fecha, tipo_mov, cantidad, precio_unitario, moneda, monto, naturaleza in filas_crudas:
        p = posiciones.setdefault(activo_id, {
            "ticker": ticker, "tipo": tipo,
            "cantidad": 0.0, "invertido_usd": 0.0,
            "ppc_nativo": 0.0, "moneda_nativa": moneda,  # solo para el fallback no-verificable
            "verificable": True, "motivo": None,
            "tuvo_ventas": False, "costo_vendido_usd": 0.0, "producido_usd": 0.0,
            "ventas_verificable": True, "motivo_ventas": None,
        })
        if tipo_mov == "evento_corporativo":
            # Split/contrasplit: cambia la cantidad, NO el costo (ni en USD
            # ni en moneda nativa) -- no es una venta, no hay ganancia
            # realizada ni plata real. El costo por unidad se ajusta solo.
            costo_nativo = p["cantidad"] * p["ppc_nativo"]
            p["cantidad"] += cantidad
            p["ppc_nativo"] = costo_nativo / p["cantidad"] if p["cantidad"] > 1e-9 else 0.0
            continue
        if tipo_mov == "compra_activo":
            # --- fallback en moneda nativa, se lleva siempre en paralelo ---
            precio_por_nominal_nativo = precio_unitario if naturaleza == "en_especie" else monto / cantidad
            costo_nativo_anterior = p["cantidad"] * p["ppc_nativo"]
            costo_nativo_nuevo = costo_nativo_anterior + cantidad * precio_por_nominal_nativo

            # --- costo en USD historico (lo que se usa si es verificable) ---
            if naturaleza == "en_especie" and ticker in DIVIDENDOS_EN_ESPECIE_CONFIRMADOS:
                precio_mercado = precio_mercado_historico(con, activo_id, fecha)
                if precio_mercado is None:
                    p["verificable"] = False
                    p["motivo"] = f"dividendo en especie confirmado, pero sin precio de mercado cargado para {fecha}"
                    aporte_usd = 0.0
                else:
                    precio_valor, precio_moneda_dividendo = precio_mercado
                    if precio_moneda_dividendo != "ARS":
                        p["verificable"] = False
                        p["motivo"] = f"precio de mercado del dividendo en moneda inesperada ({precio_moneda_dividendo!r}), no se convierte a ciegas"
                        aporte_usd = 0.0
                    else:
                        valor_ars_dividendo = valor_posicion_nativo(tipo, cantidad, precio_valor)
                        moneda_fx = "USD_CCL" if tipo in TIPOS_CCL else "USD_MEP"
                        fx_hist = precio_fx_historico(con, moneda_fx, fecha)
                        if fx_hist is None:
                            p["verificable"] = False
                            p["motivo"] = f"dividendo en especie confirmado, pero sin tipo de cambio historico para {fecha}"
                            aporte_usd = 0.0
                        else:
                            aporte_usd = valor_ars_dividendo / fx_hist
            elif naturaleza == "en_especie":
                p["verificable"] = False
                p["motivo"] = "compra en especie (canje/ajuste), sin plata real ni valor de mercado confirmado que convertir a un tipo de cambio"
                aporte_usd = 0.0  # no se suma nada al costo USD real; el fallback cubre el numero
            elif moneda in ("USD", "USD_CCL"):
                aporte_usd = monto
            elif moneda == "ARS":
                moneda_fx = "USD_CCL" if tipo in TIPOS_CCL else "USD_MEP"
                fx_hist = precio_fx_historico(con, moneda_fx, fecha)
                if fx_hist is None:
                    p["verificable"] = False
                    p["motivo"] = f"sin tipo de cambio historico cargado para {fecha}"
                    aporte_usd = 0.0
                else:
                    aporte_usd = monto / fx_hist
            else:
                p["verificable"] = False
                p["motivo"] = f"moneda de compra no reconocida: {moneda!r}"
                aporte_usd = 0.0

            p["cantidad"] += cantidad
            p["invertido_usd"] += aporte_usd
            p["ppc_nativo"] = costo_nativo_nuevo / p["cantidad"] if p["cantidad"] > 1e-9 else 0.0
        else:  # venta_activo
            p["tuvo_ventas"] = True
            costo_liberado_usd = 0.0
            if p["cantidad"] > 1e-9:
                costo_usd_por_nominal = p["invertido_usd"] / p["cantidad"]
                costo_liberado_usd = costo_usd_por_nominal * cantidad
                p["invertido_usd"] -= costo_liberado_usd
            p["cantidad"] -= cantidad

            # "Producido" de la venta: mismo criterio que "invertido" en
            # la compra -- plata REAL cobrada, no un precio_unitario x
            # cantidad recalculado (evita repetir el tipo de bug de un importador
            # documentado en ESTADO_PWOS.md, donde una fila espejo de
            # retencion en otra moneda ya viene excluida de `monto`).
            if naturaleza == "en_especie":
                p["ventas_verificable"] = False
                p["motivo_ventas"] = p["motivo_ventas"] or (
                    "venta en especie (canje/ajuste), sin plata real que convertir"
                )
            elif moneda in ("USD", "USD_CCL"):
                p["costo_vendido_usd"] += costo_liberado_usd
                p["producido_usd"] += monto
            elif moneda == "ARS":
                moneda_fx = "USD_CCL" if tipo in TIPOS_CCL else "USD_MEP"
                fx_hist = precio_fx_historico(con, moneda_fx, fecha)
                if fx_hist is None:
                    p["ventas_verificable"] = False
                    p["motivo_ventas"] = p["motivo_ventas"] or (
                        f"venta sin tipo de cambio histórico para {fecha}"
                    )
                else:
                    p["costo_vendido_usd"] += costo_liberado_usd
                    p["producido_usd"] += monto / fx_hist
            else:
                p["ventas_verificable"] = False
                p["motivo_ventas"] = p["motivo_ventas"] or f"moneda de venta no reconocida: {moneda!r}"

    resultado = []
    for activo_id, p in posiciones.items():
        # "Abierta" se decide por el neto REAL (incluye cierres via
        # renta/canje), no por p["cantidad"] (que solo netea compra/venta).
        posicion_abierta = neto_real_por_activo.get(activo_id, 0.0) > 1e-6
        tuvo_ventas = p["tuvo_ventas"]
        if not posicion_abierta and not tuvo_ventas:
            continue  # cerrada sin ventas de mercado (amortizo 100% o se canjeo, ver docstring)

        invertido_usd = None
        amortizacion_usd = None
        valor_actual_usd = None
        ganancia_porcentaje = None
        verificable_no_realizada = False
        motivo = None

        if posicion_abierta:
            precio_row = precio_actual(con, activo_id)
            if precio_row is None:
                if not tuvo_ventas:
                    continue  # mismo comportamiento de siempre: sin precio y sin ventas, no hay nada que mostrar
                motivo = "sin precio de mercado cargado"
            else:
                precio_hoy, moneda_precio = precio_row
                ultimo_precio = valor_posicion_nativo(p["tipo"], 1, precio_hoy)
                valor_actual_nativo = ultimo_precio * p["cantidad"]
                valor_actual_usd = convertir_a_usd(p["tipo"], valor_actual_nativo, moneda_precio, mep, ccl)

                # Amortizacion ya cobrada: se suma al retorno porque es
                # capital ya devuelto en efectivo, no una perdida de valor
                # de la tenencia remanente (ver amortizaciones_cobradas
                # arriba). Cada pago se convierte al MEP/CCL de SU PROPIA
                # fecha de cobro -- mismo criterio que "invertido", nunca
                # al dolar de hoy.
                amortizacion_usd = 0.0
                amortizacion_verificable = True
                for fecha_pago, monto_pago, moneda_pago in amortizaciones_cobradas(con, p["ticker"]):
                    if moneda_pago in ("USD", "USD_CCL"):
                        amortizacion_usd += monto_pago
                    elif moneda_pago == "ARS":
                        moneda_fx = "USD_CCL" if p["tipo"] in TIPOS_CCL else "USD_MEP"
                        fx_hist = precio_fx_historico(con, moneda_fx, fecha_pago)
                        if fx_hist is None:
                            amortizacion_verificable = False
                        else:
                            amortizacion_usd += monto_pago / fx_hist
                    else:
                        amortizacion_verificable = False

                # Sin fallback numerico para lo no verificable -- se probo
                # un fallback (ppc nativo x dolar de hoy) y dio un numero
                # absurdo para un bono de canje (encontrado 2026-08-20): el canje que
                # le dio origen preserva el costo en ARS dentro de
                # `precio_unitario` pero `moneda` quedo en 'USD'
                # (inconsistencia real de esos datos, no de este calculo)
                # -- convertir eso como si ya fuera dolares multiplico el
                # costo por ~1500. Mejor ningun numero que uno mal:
                # "invertido"/"ganancia" quedan en None, el ticker sigue
                # apareciendo con su valor actual (ese si es siempre
                # calculable) y el motivo, para que el usuario decida.
                verificable_total = p["verificable"] and amortizacion_verificable
                if verificable_total:
                    invertido_candidato = round(p["invertido_usd"], 2)
                    if invertido_candidato <= 1e-9:
                        motivo = "sin costo base real (posicion abierta con costo 0, no deberia pasar)"
                    else:
                        invertido_usd = invertido_candidato
                        retorno_total_usd = valor_actual_usd + amortizacion_usd
                        ganancia_porcentaje = round(((retorno_total_usd / invertido_usd) - 1) * 100, 2)
                        verificable_no_realizada = True
                elif not p["verificable"]:
                    motivo = p["motivo"]
                else:
                    motivo = "amortizacion cobrada en ARS sin tipo de cambio historico para la fecha de cobro"
        else:
            motivo = "posición cerrada (100% vendida)"

        # --- ganancia REALIZADA (2026-08-26) -- ver docstring del cambio ---
        costo_vendido_usd = producido_usd = None
        ganancia_realizada_usd = ganancia_realizada_porcentaje = None
        ganancia_realizada_verificable = None
        motivo_no_verificable_realizada = None
        if tuvo_ventas:
            ganancia_realizada_verificable = p["verificable"] and p["ventas_verificable"]
            if ganancia_realizada_verificable:
                costo_vendido_usd = round(p["costo_vendido_usd"], 2)
                producido_usd = round(p["producido_usd"], 2)
                ganancia_realizada_usd = round(producido_usd - costo_vendido_usd, 2)
                if costo_vendido_usd > 1e-9:
                    ganancia_realizada_porcentaje = round(
                        ((producido_usd / costo_vendido_usd) - 1) * 100, 2
                    )
            else:
                motivo_no_verificable_realizada = (
                    p["motivo"] if not p["verificable"] else p["motivo_ventas"]
                )

        resultado.append({
            "ticker": p["ticker"],
            "tipo": p["tipo"],  # para el filtro por categoria del panel (2026-08-20)
            "posicion_abierta": posicion_abierta,
            # Cantidad de nominales/unidades vigente (2026-09-04, pedido del
            # usuario para cruzar renglon a renglon contra su sheet/app real).
            # 0 si la posicion ya cerro del todo (compra/venta neteadas).
            "cantidad": round(p["cantidad"], 4) if posicion_abierta else 0.0,
            "invertido_usd": invertido_usd,
            "amortizacion_cobrada_usd": round(amortizacion_usd, 2) if amortizacion_usd is not None else None,
            "valor_actual_usd": round(valor_actual_usd, 2) if valor_actual_usd is not None else None,
            "ganancia_porcentaje": ganancia_porcentaje,
            "verificable": verificable_no_realizada,
            "motivo_no_verificable": motivo,
            "tuvo_ventas": tuvo_ventas,
            "costo_vendido_usd": costo_vendido_usd,
            "producido_usd": producido_usd,
            "ganancia_realizada_usd": ganancia_realizada_usd,
            "ganancia_realizada_porcentaje": ganancia_realizada_porcentaje,
            "ganancia_realizada_verificable": ganancia_realizada_verificable,
            "motivo_no_verificable_realizada": motivo_no_verificable_realizada,
        })

    # Las no verificables (ganancia_porcentaje=None) van al final, no
    # rompen el sort ni se mezclan con el ranking de las que si tienen numero.
    resultado.sort(key=lambda f: (-f["ganancia_porcentaje"]) if f["ganancia_porcentaje"] is not None else float("inf"))
    return resultado


def calcular_riesgo_por_ticker(con, hoy: date | None = None) -> dict[str, dict]:
    """Score + bucket de riesgo (database/riesgo.py) por ticker vigente,
    para el metric card "Riesgo de cartera" y la 3ra dimension de
    /api/composicion-jerarquica. Efectivo no entra (ver docstring de
    riesgo.py) -- este dict solo cubre renta fija (bonos+ONs) y equity
    (acciones+CEDEARs), las 2 categorias que la metodologia cubre.

    Rating/legislacion/tipo de instrumento de renta fija se resuelven
    con las constantes de riesgo.py + bonos_referencia/
    cronograma_pagos_bonos (sin red); beta/Deuda-Patrimonio salen de
    `factores_riesgo` (cacheados por cargar_factores_riesgo.py, no se
    pega contra yfinance en cada carga del dashboard)."""
    hoy = hoy or configuracion.hoy()
    resultado: dict[str, dict] = {}

    filas_rf = con.execute("""
        SELECT a.ticker, a.tipo AS tipo_activo, br.legislacion,
               MAX(c.fecha_pago) AS vencimiento, fr.deuda_patrimonio
        FROM activos a
        JOIN bonos_referencia br ON br.activo_id = a.id
        JOIN cronograma_pagos_bonos c ON c.activo_id = a.id
        LEFT JOIN factores_riesgo fr ON fr.activo_id = a.id
        GROUP BY a.id;
    """).fetchall()
    for ticker, tipo_activo, legislacion, vencimiento, deuda_patrimonio in filas_rf:
        anios = anios_a_vencimiento(date.fromisoformat(vencimiento), hoy)
        if tipo_activo == "on":
            rating = None
            tipo_instrumento_categoria = "on_estandar"
        else:
            emisor = TICKER_EMISOR_SOBERANO.get(ticker)
            rating = RATING_EMISOR_SOBERANO.get(emisor)
            deuda_patrimonio = None
            tipo_instrumento_categoria = TICKER_TIPO_INSTRUMENTO_SOBERANO.get(ticker)

        r = calcular_score_renta_fija(
            rating=rating,
            deuda_patrimonio=deuda_patrimonio,
            anios_vencimiento=anios,
            legislacion=legislacion,
            tipo_instrumento_categoria=tipo_instrumento_categoria,
        )
        resultado[ticker] = {"score_final": r["score_final"], "bucket": r["bucket"], "tipo_activo": tipo_activo}

    filas_equity = con.execute("""
        SELECT a.ticker, a.tipo AS tipo_activo, fr.beta
        FROM activos a
        JOIN factores_riesgo fr ON fr.activo_id = a.id
        WHERE a.tipo IN ('accion', 'cedear');
    """).fetchall()
    for ticker, tipo_activo, beta in filas_equity:
        r = calcular_score_equity(beta, tipo_activo)
        resultado[ticker] = {"score_final": r["score_final"], "bucket": r["bucket"], "tipo_activo": tipo_activo}

    return resultado


def mostrar_tablero():
    con = sqlite3.connect(DB_PATH)
    mep = obtener_precio_fx(con, "USD_MEP")
    ccl = obtener_precio_fx(con, "USD_CCL")

    saldos = valorizar_saldos_cuenta(con, mep)
    posiciones = valorizar_posiciones(con, mep, ccl)

    print("=" * 50)
    print(f"  PWOS — Patrimonio en USD (MEP: {mep} · CCL: {ccl}, CEDEARs vía CCL)")
    print("=" * 50)

    print("\n💵 EFECTIVO")
    total_efectivo = 0.0
    for f in saldos:
        print(f"   {f['concepto']:<35} USD {f['valor_usd']:>10.2f}")
        total_efectivo += f["valor_usd"]

    print("\n📈 INVERSIONES (por tipo de activo)")
    total_inversiones = sum(
        f["valor_usd"] for f in posiciones if f["valor_usd"] is not None
    )

    def _orden_tipo(tipo: str) -> tuple:
        return (ORDEN_TIPOS.index(tipo),) if tipo in ORDEN_TIPOS else (len(ORDEN_TIPOS), tipo)

    tipos_presentes = sorted({f["tipo"] for f in posiciones}, key=_orden_tipo)

    for tipo in tipos_presentes:
        filas_tipo = sorted(
            (f for f in posiciones if f["tipo"] == tipo),
            key=lambda f: f["ticker"],
        )
        print(f"\n  {tipo.upper()}")
        subtotal = 0.0
        for f in filas_tipo:
            concepto = f"{f['contenedor']} · {f['ticker']} x{f['cantidad']}"
            if f["valor_usd"] is None:
                print(f"     {concepto:<35} (sin precio cargado)")
                continue
            print(f"     {concepto:<35} USD {f['valor_usd']:>10.2f}")
            subtotal += f["valor_usd"]
        porcentaje = (subtotal / total_inversiones * 100) if total_inversiones else 0.0
        print(f"   {'Subtotal ' + tipo:<38} USD {subtotal:>10.2f}  ({porcentaje:.1f}%)")

    total = total_efectivo + total_inversiones
    print("\n" + "-" * 50)
    print(f"   TOTAL EFECTIVO:      USD {total_efectivo:>10.2f}")
    print(f"   TOTAL INVERSIONES:   USD {total_inversiones:>10.2f}")
    print(f"   PATRIMONIO TOTAL:    USD {total:>10.2f}")
    print("=" * 50)

    con.close()


if __name__ == "__main__":
    mostrar_tablero()
