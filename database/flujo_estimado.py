"""
PWOS -- Flujo de cobros estimado (ticket "Flujo de pagos mensual: estimado
vs. real", Tanda 1, 2026-10-05). Schema v2.45 (migrar_v245_flujo_estimado.py).

Pedido del usuario: todo cobro (renta de bonos, dividendos, alquileres) arranca
como ESTIMADO, figura en el flujo en su fecha exacta aunque la ronda
incremental todavia no lo haya confirmado, y cuando entra a la cuenta se
corrige con el real y se ve la diferencia. Por eso el estimado se GUARDA en
`flujos_esperados` (no se recalcula al vuelo cada vez): si no, no habria
contra que comparar.

Ciclo de vida de una fila generada aca:
  - `pendiente` con fecha FUTURA: se recalcula en cada sincronizacion
    (cambia la tenencia, el MEP, el contrato). Si el evento deja de existir
    (se vendio el bono), la fila se borra.
  - `pendiente` con fecha <= hoy: queda FIJA -- es el estimado contra el que
    se compara el real ("a confirmar" en pantalla). No se recalcula ni se
    borra.
  - `cobrado` / `no_cobrado`: lo decide la conciliacion con la ronda
    incremental (Tanda 3). Nunca se toca desde aca.

Fuentes de esta tanda:
  - Bonos ('cronograma'): `cronograma_pagos_bonos` x tenencia vigente POR
    CONTENEDOR (`vista_posiciones`) -- una fila por broker donde entra la
    plata (un mismo ticker puede estar en dos brokers). Mismo calculo que
    `vista_flujo_fondos_bonos`: USD paga en USD, ARS en pesos,
    USD_LINKED en pesos (el cronograma de Rava ya viene en pesos al A3500).
    Bonos sin `moneda_pago` clasificada quedan afuera, igual que en la vista.
  - Alquileres ('alquiler'): contrato vigente de `propiedades`
    (`alquiler_monto`/`_moneda`/`_dia_cobro`/`_contenedor_id`/`_desde`),
    un cobro por mes durante HORIZONTE_ALQUILER_MESES.
  - Dividendos ('yfinance', Tanda 2, schema v2.46): `dividendos_proyectados`
    (monto bruto por accion, lo llena cargar_dividendos_proyectados.py) x
    tenencia por contenedor / ratio del CEDEAR x factor neto. CEDEAR en USD,
    accion local en ARS. Si la tabla no existe todavia (base sin v2.46) se
    omiten los dividendos.

Sincronizar es local (sin red) y solo escribe si algo cambio, por eso se
puede llamar en cada lectura del flujo (api.py) sin depender de un cron.
"""
import calendar
from datetime import date, datetime

from calendario_flujo import flujos_otros_pasivos
from dashboard import obtener_precio_fx, precio_fx_historico
from valorizacion import parte_propia, pct_de, pct_propiedad_por_cuenta, solo_cierre
import configuracion

ORIGENES_GENERADOS = ("cronograma", "alquiler", "yfinance")
# Factor neto = lo que acredita el broker / dividendo bruto. Calibrado el
# 2026-10-05 contra la solapa DIVIDENDO del sheet del usuario
# (calibrar_dividendos.py, 2025-2026): CEDEARs de EE.UU. 0,63-0,65 en 40+
# cobros (30% de retencion de EE.UU. + ~7% de gastos), un CEDEAR del Reino Unido (sin
# retencion de EE.UU.) 0,87-0,90, una accion local 0,95 (7% de impuesto
# cedular sobre dividendos -> 0,93).
FACTOR_NETO_CEDEAR_EEUU = 0.64
FACTOR_NETO_CEDEAR_OTRO_PAIS = 0.90
FACTOR_NETO_ACCION_LOCAL = 0.93
# Objetivo de renta mensual que el usuario se propone para 2027 (2026-10-05):
# se mide contra la RENTA PURA (interes + dividendos + alquileres + interes
# bancario), no contra la amortizacion, que es capital que vuelve.
OBJETIVO_RENTA_MENSUAL_USD = 650.0
TIPOS_RENTA = ("cupon", "dividendo", "alquiler", "otros")
# Tanda 3: el panel muestra tambien los meses ya pasados (estimado vs. real),
# desde que arranco el seguimiento (antes no hay estimados guardados con que
# comparar) y como mucho MESES_HISTORIA hacia atras.
INICIO_SEGUIMIENTO = "2026-10-01"
MESES_HISTORIA = 6
HORIZONTE_ALQUILER_MESES = 12
# Un pago de bono del mes en curso que el importador de un broker ya registro como
# cobrado (filas 'importador', sin contenedor) no se vuelve a estimar: misma
# especie y tipo, a +-DIAS_TOLERANCIA_IMPORTADOR de la fecha del cronograma.
DIAS_TOLERANCIA_IMPORTADOR = 5
# Diferencia que se ignora al comparar un estimado viejo con el nuevo
# (evita reescribir por ruido de redondeo del MEP).
TOLERANCIA_MONTO = 0.005


def _sumar_meses(d: date, n: int) -> tuple[int, int]:
    total = d.year * 12 + (d.month - 1) + n
    return total // 12, total % 12 + 1


def _fecha_en_mes(anio: int, mes: int, dia: int) -> date:
    return date(anio, mes, min(dia, calendar.monthrange(anio, mes)[1]))


def eventos_bonos(con, desde: date, mep: float, a3500: float) -> list[dict]:
    filas = con.execute(
        """
        SELECT vp.activo_id, vp.contenedor_id, c.fecha_pago, c.tipo_pago,
               c.monto_por_vn * vp.cantidad / 100.0, r.moneda_pago
        FROM cronograma_pagos_bonos c
        JOIN vista_posiciones vp ON vp.activo_id = c.activo_id
        JOIN bonos_referencia r ON r.activo_id = c.activo_id
        WHERE c.fecha_pago >= ? AND r.moneda_pago IS NOT NULL
          AND c.monto_por_vn > 0 AND vp.cantidad > 0;
        """,
        (desde.isoformat(),),
    ).fetchall()
    eventos = []
    for activo_id, contenedor_id, fecha, tipo_pago, monto, moneda_pago in filas:
        if moneda_pago == "USD":
            moneda, monto_usd = "USD", monto
        elif moneda_pago == "USD_LINKED":
            moneda, monto_usd = "ARS", monto / a3500
        else:
            moneda, monto_usd = "ARS", monto / mep
        eventos.append({
            "activo_id": activo_id, "propiedad_id": None, "contenedor_id": contenedor_id,
            "tipo_flujo": "cupon" if tipo_pago == "interes" else "amortizacion",
            "fecha_estimada": fecha, "monto_estimado": round(monto, 2), "moneda": moneda,
            "monto_estimado_usd": round(monto_usd, 2), "origen_estimado": "cronograma",
        })
    return eventos


def eventos_dividendos(con, desde: date, mep: float) -> list[dict]:
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'dividendos_proyectados';").fetchone() is None:
        return []
    filas = con.execute(
        """
        SELECT vp.activo_id, vp.contenedor_id, vp.tipo_activo, vp.cantidad,
               d.fecha_pago, d.monto_por_accion, d.moneda, rc.ratio, rc.pais
        FROM dividendos_proyectados d
        JOIN vista_posiciones vp ON vp.activo_id = d.activo_id
        LEFT JOIN ratios_cedear rc ON rc.ticker_byma = vp.ticker
        WHERE d.fecha_pago >= ? AND vp.cantidad > 0;
        """,
        (desde.isoformat(),),
    ).fetchall()
    eventos = []
    for activo_id, contenedor_id, tipo, cantidad, fecha, dps, moneda, ratio, pais in filas:
        if tipo == "accion":
            monto = dps * cantidad * FACTOR_NETO_ACCION_LOCAL
        elif ratio:
            factor = FACTOR_NETO_CEDEAR_EEUU if pais == "Estados Unidos" else FACTOR_NETO_CEDEAR_OTRO_PAIS
            monto = dps * cantidad / ratio * factor
        else:
            continue  # CEDEAR sin ratio conocido: no se adivina
        if monto <= 0:
            continue
        eventos.append({
            "activo_id": activo_id, "propiedad_id": None, "contenedor_id": contenedor_id,
            "tipo_flujo": "dividendo", "fecha_estimada": fecha, "monto_estimado": round(monto, 2),
            "moneda": moneda, "monto_estimado_usd": round(monto if moneda == "USD" else monto / mep, 2),
            "origen_estimado": "yfinance",
        })
    return eventos


def eventos_alquileres(con, hoy: date, mep: float) -> list[dict]:
    filas = con.execute(
        """
        SELECT id, alquiler_monto, alquiler_moneda, alquiler_dia_cobro,
               alquiler_contenedor_id, alquiler_desde
        FROM propiedades
        WHERE alquiler_monto IS NOT NULL AND alquiler_moneda IS NOT NULL
          AND alquiler_dia_cobro IS NOT NULL AND alquiler_desde IS NOT NULL;
        """
    ).fetchall()
    eventos = []
    for propiedad_id, monto, moneda, dia, contenedor_id, desde in filas:
        inicio = max(date.fromisoformat(desde).replace(day=1), hoy.replace(day=1))
        for i in range(HORIZONTE_ALQUILER_MESES):
            anio, mes = _sumar_meses(inicio, i)
            fecha = _fecha_en_mes(anio, mes, dia)
            if fecha < date.fromisoformat(desde):
                continue
            eventos.append({
                "activo_id": None, "propiedad_id": propiedad_id, "contenedor_id": contenedor_id,
                "tipo_flujo": "alquiler", "fecha_estimada": fecha.isoformat(),
                "monto_estimado": round(monto, 2), "moneda": moneda,
                "monto_estimado_usd": round(monto if moneda == "USD" else monto / mep, 2),
                "origen_estimado": "alquiler",
            })
    return eventos


def _clave(e) -> tuple:
    return (e["tipo_flujo"], e["activo_id"], e["propiedad_id"], e["contenedor_id"], e["fecha_estimada"])


def _ya_cobrado_por_importador(con, e) -> bool:
    if e["activo_id"] is None:
        return False
    return con.execute(
        """
        SELECT 1 FROM flujos_esperados
        WHERE origen_estimado = 'importador' AND activo_id = ? AND tipo_flujo = ?
          AND ABS(julianday(fecha_estimada) - julianday(?)) <= ?;
        """,
        (e["activo_id"], e["tipo_flujo"], e["fecha_estimada"], DIAS_TOLERANCIA_IMPORTADOR),
    ).fetchone() is not None


@solo_cierre()  # historia / escribe en la base: siempre con el cierre (ADR-033)
def planificar(con, hoy: date | None = None) -> dict:
    """Calcula que habria que insertar/actualizar/borrar, SIN escribir.
    Ventana: desde el 1ro del mes en curso (un pago de hoy o de dias atras
    del mismo mes tiene que figurar aunque nunca se haya estimado antes)."""
    hoy = hoy or configuracion.hoy()
    mep = obtener_precio_fx(con, "USD_MEP")
    a3500 = obtener_precio_fx(con, "USD_A3500")
    inicio_mes = hoy.replace(day=1)

    nuevos = {_clave(e): e for e in (eventos_bonos(con, inicio_mes, mep, a3500)
                                     + eventos_dividendos(con, inicio_mes, mep)
                                     + eventos_alquileres(con, hoy, mep))}

    marcadores = ",".join("?" * len(ORIGENES_GENERADOS))
    existentes = {}
    for fila in con.execute(
        f"""
        SELECT id, tipo_flujo, activo_id, propiedad_id, contenedor_id, fecha_estimada,
               monto_estimado, moneda, monto_estimado_usd, estado
        FROM flujos_esperados WHERE origen_estimado IN ({marcadores});
        """,
        ORIGENES_GENERADOS,
    ):
        (id_, tipo, activo_id, propiedad_id, contenedor_id, fecha, monto, moneda, monto_usd, estado) = fila
        existentes[(tipo, activo_id, propiedad_id, contenedor_id, fecha)] = {
            "id": id_, "monto_estimado": monto, "moneda": moneda,
            "monto_estimado_usd": monto_usd, "estado": estado,
        }

    insertar, actualizar, borrar = [], [], []
    for clave, e in nuevos.items():
        viejo = existentes.get(clave)
        futuro = e["fecha_estimada"] > hoy.isoformat()
        if viejo is None:
            if not _ya_cobrado_por_importador(con, e):
                insertar.append(e)
        elif viejo["estado"] == "pendiente" and futuro and (
            abs(viejo["monto_estimado"] - e["monto_estimado"]) > TOLERANCIA_MONTO
            or abs((viejo["monto_estimado_usd"] or 0) - e["monto_estimado_usd"]) > TOLERANCIA_MONTO
            or viejo["moneda"] != e["moneda"]
        ):
            actualizar.append({**e, "id": viejo["id"]})
    for clave, viejo in existentes.items():
        if clave not in nuevos and viejo["estado"] == "pendiente" and clave[4] > hoy.isoformat():
            borrar.append(viejo["id"])
    return {"insertar": insertar, "actualizar": actualizar, "borrar": borrar}


@solo_cierre()  # historia / escribe en la base: siempre con el cierre (ADR-033)
def sincronizar(con, hoy: date | None = None) -> dict:
    """Aplica `planificar` en una transaccion. Devuelve los conteos. No
    escribe nada (ni abre transaccion) si no hay cambios."""
    plan = planificar(con, hoy)
    conteo = {k: len(v) for k, v in plan.items()}
    if not any(conteo.values()):
        return conteo
    ahora = datetime.now().isoformat(timespec="seconds")
    with con:
        con.executemany(
            """
            INSERT INTO flujos_esperados
                (activo_id, propiedad_id, contenedor_id, tipo_flujo, fecha_estimada,
                 monto_estimado, moneda, monto_estimado_usd, origen_estimado, estimado_en, estado)
            VALUES (:activo_id, :propiedad_id, :contenedor_id, :tipo_flujo, :fecha_estimada,
                    :monto_estimado, :moneda, :monto_estimado_usd, :origen_estimado, :ahora, 'pendiente');
            """,
            [{**e, "ahora": ahora} for e in plan["insertar"]],
        )
        con.executemany(
            """
            UPDATE flujos_esperados
            SET monto_estimado = :monto_estimado, moneda = :moneda,
                monto_estimado_usd = :monto_estimado_usd, estimado_en = :ahora
            WHERE id = :id AND estado = 'pendiente';
            """,
            [{**e, "ahora": ahora} for e in plan["actualizar"]],
        )
        con.executemany(
            "DELETE FROM flujos_esperados WHERE id = ? AND estado = 'pendiente';",
            [(i,) for i in plan["borrar"]],
        )
    return conteo


def _usd_de_real(con, monto: float, moneda: str, fecha: str, mep: float) -> float:
    if moneda == "USD":
        return monto
    fx = precio_fx_historico(con, "USD_MEP", fecha)
    return monto / (fx if fx is not None else mep)


def flujo_cobros(con, hoy: date | None = None, ambiguos: dict | None = None) -> dict:
    """Lo que muestra el panel "Flujo de pagos mensual": cada cobro desde el
    inicio del seguimiento (INICIO_SEGUIMIENTO / MESES_HISTORIA) con su
    fecha exacta, donde entra, monto en su moneda y en USD, y estado:
      - 'estimado'    pendiente con fecha futura
      - 'a_confirmar' pendiente con fecha <= hoy (ya deberia haber entrado;
                      la ronda incremental lo confirma)
      - 'cobrado'     vinculado a un movimiento real (monto real + diferencia
                      contra el estimado, si lo hubo)
      - 'no_cobrado'  la ronda cubrio la fecha y no entro (no suma a la renta)
    + el interes bancario real ya cobrado ('otros', siempre 'cobrado') y el
    agregado por mes (renta pura separada de la amortizacion, y estimado vs.
    real de lo ya cobrado). `ambiguos` (de conciliar_flujos.conciliar):
    flujo_id -> movimientos posibles, se adjunta como `candidatos`."""
    hoy = hoy or configuracion.hoy()
    ambiguos = ambiguos or {}
    mep = obtener_precio_fx(con, "USD_MEP")
    anio_h, mes_h = _sumar_meses(hoy.replace(day=1), -MESES_HISTORIA)
    inicio_mes = min(max(INICIO_SEGUIMIENTO, f"{anio_h:04d}-{mes_h:02d}-01"), hoy.replace(day=1).isoformat())

    eventos = []
    pcts = pct_propiedad_por_cuenta(con)
    for (id_, fecha, tipo, concepto, contenedor, moneda, monto, monto_usd, origen, estado,
         real_monto, real_moneda, real_fecha, real_cuenta) in con.execute(
        """
        SELECT f.id, f.fecha_estimada, f.tipo_flujo, COALESCE(a.ticker, p.alias), co.nombre,
               f.moneda, f.monto_estimado, f.monto_estimado_usd, f.origen_estimado, f.estado,
               m.monto, m.moneda, m.fecha, m.cuenta_destino_id
        FROM flujos_esperados f
        LEFT JOIN activos a ON a.id = f.activo_id
        LEFT JOIN propiedades p ON p.id = f.propiedad_id
        LEFT JOIN contenedores co ON co.id = f.contenedor_id
        LEFT JOIN movimientos m ON m.id = f.movimiento_id
        WHERE f.fecha_estimada >= ?
        ORDER BY f.fecha_estimada, f.id;
        """,
        (inicio_mes,),
    ):
        es_estimado_real = origen != "importador"
        if monto_usd is None:
            monto_usd = _usd_de_real(con, monto, moneda, fecha, mep)
        evento = {
            "id": id_, "fecha": fecha, "tipo": tipo, "concepto": concepto, "contenedor": contenedor,
            "moneda": moneda, "monto": round(monto, 2), "monto_usd": round(monto_usd, 2),
            "origen": origen, "estado": estado,
            "monto_real": None, "monto_real_usd": None, "diferencia_usd": None,
            "candidatos": ambiguos.get(id_, []),
        }
        if estado == "pendiente":
            evento["estado"] = "a_confirmar" if fecha <= hoy.isoformat() else "estimado"
        elif estado == "cobrado" and real_monto is not None:
            # Cobro en un contenedor compartido: la parte propia (ADR-032),
            # la misma en que esta el estimado.
            real_monto = parte_propia(real_monto, pct_de(pcts, real_cuenta))
            real_usd = _usd_de_real(con, real_monto, real_moneda, real_fecha, mep)
            evento.update({"monto_real": round(real_monto, 2), "monto_real_usd": round(real_usd, 2)})
            if es_estimado_real:
                evento["diferencia_usd"] = round(real_usd - monto_usd, 2)
        eventos.append(evento)

    for otro in flujos_otros_pasivos(con, mep, inicio_mes[:7]):
        if otro["monto_usd"] <= 0:  # un banco registra capitalizaciones de $0,00
            continue
        eventos.append({
            "id": None, "fecha": otro["fecha"], "tipo": "otros", "concepto": otro["concepto"],
            "contenedor": otro["concepto"], "moneda": None, "monto": None,
            "monto_usd": otro["monto_usd"], "origen": "movimiento", "estado": "cobrado",
            "monto_real": None, "monto_real_usd": otro["monto_usd"], "diferencia_usd": None,
            "candidatos": [],
        })
    eventos.sort(key=lambda e: (e["fecha"], e["tipo"]))

    def _mes_vacio(mes):
        return {"mes": mes, "por_tipo": {t: 0.0 for t in ("cupon", "amortizacion", "dividendo", "alquiler", "otros")},
                "por_moneda": {"ARS": 0.0, "USD": 0.0}, "cobrado_usd": 0.0, "pendiente_usd": 0.0,
                "estimado_cobrado_usd": 0.0, "real_cobrado_usd": 0.0, "no_cobrado_usd": 0.0}

    por_mes = {}
    for e in eventos:
        fila = por_mes.setdefault(e["fecha"][:7], _mes_vacio(e["fecha"][:7]))
        if e["estado"] == "no_cobrado":
            fila["no_cobrado_usd"] += e["monto_usd"]
            continue
        if e["diferencia_usd"] is not None:
            fila["estimado_cobrado_usd"] += e["monto_usd"]
            fila["real_cobrado_usd"] += e["monto_real_usd"]
        usd = e["monto_real_usd"] if e["monto_real_usd"] is not None else e["monto_usd"]
        fila["por_tipo"][e["tipo"]] += usd
        if e["moneda"] in fila["por_moneda"]:
            fila["por_moneda"][e["moneda"]] += e["monto_real"] if e["monto_real"] is not None else e["monto"]
        fila["cobrado_usd" if e["estado"] == "cobrado" else "pendiente_usd"] += usd

    # Meses sin ningun cobro aparecen en 0: un hueco real no puede desaparecer.
    if por_mes:
        ultimo = max(por_mes)
        anio, mes = int(inicio_mes[:4]), int(inicio_mes[5:7])
        while f"{anio:04d}-{mes:02d}" <= ultimo:
            por_mes.setdefault(f"{anio:04d}-{mes:02d}", _mes_vacio(f"{anio:04d}-{mes:02d}"))
            anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)

    meses = sorted(por_mes.values(), key=lambda f: f["mes"])
    for f in meses:
        f["por_tipo"] = {k: round(v, 2) for k, v in f["por_tipo"].items()}
        f["por_moneda"] = {k: round(v, 2) for k, v in f["por_moneda"].items()}
        f["renta_usd"] = round(sum(f["por_tipo"][t] for t in TIPOS_RENTA), 2)
        f["amortizacion_usd"] = f["por_tipo"]["amortizacion"]
        f["total_usd"] = round(f["renta_usd"] + f["amortizacion_usd"], 2)
        f["cobrado_usd"] = round(f["cobrado_usd"], 2)
        f["pendiente_usd"] = round(f["pendiente_usd"], 2)
        f["no_cobrado_usd"] = round(f["no_cobrado_usd"], 2)
        f["estimado_cobrado_usd"] = round(f["estimado_cobrado_usd"], 2)
        f["real_cobrado_usd"] = round(f["real_cobrado_usd"], 2)
        f["diferencia_usd"] = round(f["real_cobrado_usd"] - f["estimado_cobrado_usd"], 2)

    return {"objetivo_renta_usd": OBJETIVO_RENTA_MENSUAL_USD, "hoy": hoy.isoformat(),
            "mes_actual": hoy.isoformat()[:7], "meses": meses, "eventos": eventos}
