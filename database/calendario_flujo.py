"""
PWOS -- Herramienta 2, paso 1 (Epica 11): calendario de flujo mensual y
huecos de renta. Reusa `vista_flujo_fondos_bonos` (ya agrega monto REAL
en USD -- tenencia vigente x cronograma, resuelve interes/amortizacion
y USD/ARS/USD_LINKED via MEP/A3500) en vez de reimplementar el calculo
de conversion a USD en un tercer lugar.

`mes_flojo`: umbral configurable (default 50% del promedio de la
ventana, punto de partida sugerido por el brief de la Epica 11) -- un
piso fijo en USD tambien es valido, se pasa aparte si el usuario lo define.

2026-09-17: el calendario solo mostraba renta
PROYECTADA de bonos, dejando afuera ingreso pasivo REAL de otros
contenedores (interes de plazo fijo/saldo remunerado en un banco,
alquiler que entra por una billetera virtual). Se suma una 2da fuente,
`_flujos_otros_pasivos` -- movimientos `tipo='renta'` YA CARGADOS (no
proyectados, mismo criterio de "solo lo que ya paso" que
renta_dividendos.py) en cualquier contenedor `tipo IN ('banco',
'billetera_virtual')`. Ese filtro estructural (no una lista de nombres
a mano) nunca puede duplicar un cupon de bono ya contado por
`vista_flujo_fondos_bonos` -- las tenencias de bonos SIEMPRE viven en un
contenedor `tipo='broker'`, nunca en un banco o billetera. Por el mismo
motivo, un fondo inmobiliario (tipo='broker') queda afuera del todo: investigado
contra la base real, no tiene NINGUN movimiento `tipo='renta'` -- es
solo un ajuste manual de valuacion (`alta_inmueble.py`), y mezclar una
revalorizacion de activo con efectivo realmente cobrado seria un
error conceptual, no solo un hueco de datos.

Tambien se suma `detalle` por mes: la lista de movimientos individuales
(bonos + otros) con su fecha REAL de pago -- antes la tabla solo
mostraba el mes agregado, sin forma de ver el dia exacto de cada cobro.

`flujos_otros_pasivos` es publica (usada tambien por api.py para el
grafico de "Flujo de pagos mensual" del Dashboard, 2026-09-17) -- un
solo lugar para el filtro estructural + la exclusion manual, no
reimplementado en cada endpoint que la necesite.

2026-09-17 (continuacion): se suma una 3ra fuente,
`alquileres_usd`, leyendo la tabla nueva `alquileres` (ver
propiedades.py/alquileres.py/migrar_v227). la billetera donde se cobra un alquiler se agrego a
CONTENEDORES_EXCLUIDOS_OTROS_PASIVOS en este mismo cambio -- el
alquiler del depto que antes se contaba via `otros_usd` (movimiento
`renta` en esa billetera) ahora vive SOLO en `alquileres`, contarlo en
los 2 lados duplicaria el ingreso. `otros_usd` queda para interes
bancario genuino (bancos y billeteras) -- pendiente de decidir en
otro ticket si tambien migra a un esquema propio.

2026-10-05 (ticket "Flujo de pagos mensual: estimado vs. real", Tanda 3):
`calendario_flujo_mensual` deja de calcular por su cuenta y lee la MISMA
fuente que el panel del Dashboard (`flujo_estimado.flujo_cobros`: estimados
guardados de bonos por contenedor, dividendos y alquileres segun contrato,
conciliados con lo real). Mismo formato de salida + `dividendos_usd`;
`es_estimado` = todavia no confirmado por la ronda incremental. Se borraron
`_flujos_bonos` y `_flujos_alquileres` (sin otros usos). `flujos_otros_pasivos`
sigue aca (la usan flujo_estimado.py y api.py)."""
from datetime import date

import configuracion
import dashboard  # precio_fx_historico -- mismo helper que renta_dividendos.py
from valorizacion import parte_propia, pct_de, pct_propiedad_por_cuenta

# Contenedores tipo=banco/billetera_virtual que el filtro estructural
# agarraria pero el usuario pidio excluir a mano.
# - Una billetera sin plazo fijo ni saldo hoy: sus `Rendimiento diario`
#   viejos son de un saldo que ya no existe. A diferencia de un fondo inmobiliario (afuera por construccion,
#   nunca tuvo un movimiento `renta` real), esta es una exclusion
#   DELIBERADA sobre un contenedor que si tiene datos reales --
#   ampliable si se cierra otra cuenta (lista en la configuracion).
# - La billetera del alquiler (2026-09-17): su alquiler ahora vive en la tabla
#   `alquileres` (ver `_flujos_alquileres`), no en `otros_usd` --
#   dejarlo aca duplicaria el mismo ingreso en los 2 buckets.
CONTENEDORES_EXCLUIDOS_OTROS_PASIVOS = set(configuracion.get("contenedores_excluidos_otros_pasivos", default=[]))

# Categorias (y sus hijas) que NO son interes bancario aunque entren como
# `renta` a un banco/billetera (2026-10-26, ADR-032): el alquiler que llega a
# una cuenta compartida ya se cuenta en la tabla `alquileres`. Por
# categoria y no por contenedor (decision del usuario): el rendimiento de esa
# misma cuenta SI es interes, y cuenta en la parte propia.
CATEGORIAS_EXCLUIDAS_OTROS_PASIVOS = ("Alquileres",)


def flujos_otros_pasivos(con, mep: float, mes_desde: str, mes_hasta: str | None = None) -> list[dict]:
    """Ingreso pasivo REAL ya cobrado (no proyectado) en un banco o
    billetera virtual -- interes de plazo fijo/saldo remunerado, alquiler
    acreditado. `cuenta_destino_id` (no origen): mismo filtro de
    seguridad que renta_dividendos.py -- un ingreso real siempre acredita
    una cuenta, un `cuenta_origen_id` sin destino seria una fila espejo/
    retencion, no plata que efectivamente entro. `mes_hasta=None` (usado
    por el grafico de Dashboard, sin tope de horizonte) trae todo desde
    `mes_desde` en adelante -- en la practica nunca hay filas en el
    futuro (son movimientos ya cargados), no hace falta acotar."""
    excluidos = tuple(CONTENEDORES_EXCLUIDOS_OTROS_PASIVOS)
    placeholders_excluidos = ",".join("?" * len(excluidos))
    placeholders_categorias = ",".join("?" * len(CATEGORIAS_EXCLUIDAS_OTROS_PASIVOS))
    condicion_hasta = "AND strftime('%Y-%m', m.fecha) < ?" if mes_hasta is not None else ""
    parametros = (*CATEGORIAS_EXCLUIDAS_OTROS_PASIVOS, *excluidos, mes_desde,
                  *([mes_hasta] if mes_hasta is not None else []))
    filas = con.execute(
        f"""
        WITH RECURSIVE categorias_excluidas(id) AS (
            SELECT id FROM categorias WHERE nombre IN ({placeholders_categorias})
            UNION ALL
            SELECT c.id FROM categorias c JOIN categorias_excluidas ce ON c.categoria_padre_id = ce.id
        )
        SELECT co.nombre, m.fecha, m.moneda, m.monto, m.descripcion, c.id
        FROM movimientos m
        JOIN cuentas c ON c.id = m.cuenta_destino_id
        JOIN contenedores co ON co.id = c.contenedor_id
        WHERE m.tipo = 'renta' AND m.naturaleza = 'efectivo'
          AND co.tipo IN ('banco', 'billetera_virtual')
          AND co.nombre NOT IN ({placeholders_excluidos})
          AND (m.categoria_id IS NULL OR m.categoria_id NOT IN (SELECT id FROM categorias_excluidas))
          AND strftime('%Y-%m', m.fecha) >= ?
          {condicion_hasta}
        ORDER BY m.fecha;
        """,
        parametros,
    ).fetchall()

    pcts = pct_propiedad_por_cuenta(con)  # contenedores compartidos: solo la parte propia (ADR-032)
    resultado = []
    for contenedor, fecha, moneda, monto, descripcion, cuenta_id in filas:
        monto = parte_propia(monto, pct_de(pcts, cuenta_id))
        if moneda == "USD":
            monto_usd, es_estimado = monto, False
        else:
            fx = dashboard.precio_fx_historico(con, "USD_MEP", fecha)
            es_estimado = fx is None
            monto_usd = monto / (fx if fx is not None else mep)
        resultado.append({
            "fecha": fecha, "tipo": "otros", "concepto": contenedor,
            "monto_usd": round(monto_usd, 2), "es_estimado": es_estimado,
            "descripcion": descripcion,
        })
    return resultado


# Tipos de flujo_estimado -> claves que ya usaba el calendario (frontend y
# reporte_calendario_flujo.py).
_CLAVE_CALENDARIO = {"cupon": "interes", "amortizacion": "amortizacion", "otros": "otros",
                     "alquiler": "alquileres", "dividendo": "dividendos"}


def calendario_flujo_mensual(con, mep: float | None = None, meses: int = 12, umbral_pct_promedio: float = 0.5,
                              piso_usd: float | None = None) -> dict:
    """Serie de los proximos `meses` meses calendario (incluye el mes
    actual) con interes/amortizacion/otros/alquileres/dividendos/total en
    USD, `detalle` (cada cobro con su fecha real) y `tiene_estimado` (algun
    cobro del mes todavia no confirmado por la ronda incremental).
    `mes_flojo=True` si el total del mes cae por debajo del umbral:
    `piso_usd` si se paso, si no `promedio_ventana * umbral_pct_promedio`.
    `mep` ya no se usa (lo resuelve flujo_cobros); queda por compatibilidad.
    Un cobro 'no_cobrado' no suma (no entro)."""
    import flujo_estimado  # import local: flujo_estimado importa flujos_otros_pasivos de aca

    datos = flujo_estimado.flujo_cobros(con)
    hoy = configuracion.hoy()
    claves_ventana = []
    for i in range(meses):
        anio = hoy.year + (hoy.month - 1 + i) // 12
        mes = (hoy.month - 1 + i) % 12 + 1
        claves_ventana.append(f"{anio:04d}-{mes:02d}")

    serie = {m: {"mes": m, "interes_usd": 0.0, "amortizacion_usd": 0.0, "otros_usd": 0.0,
                 "alquileres_usd": 0.0, "dividendos_usd": 0.0, "tiene_estimado": False, "detalle": []}
             for m in claves_ventana}
    for e in datos["eventos"]:
        fila = serie.get(e["fecha"][:7])
        if fila is None or e["estado"] == "no_cobrado":
            continue
        clave = _CLAVE_CALENDARIO[e["tipo"]]
        usd = e["monto_real_usd"] if e["monto_real_usd"] is not None else e["monto_usd"]
        es_estimado = e["estado"] != "cobrado"
        fila[f"{clave}_usd"] += usd
        fila["tiene_estimado"] = fila["tiene_estimado"] or es_estimado
        descripcion = (f"{e['moneda']} {e['monto_real'] if e['monto_real'] is not None else e['monto']:,.2f}"
                       if e["moneda"] else None)
        if e["contenedor"]:
            descripcion = f"{descripcion} -- {e['contenedor']}" if descripcion else e["contenedor"]
        fila["detalle"].append({"fecha": e["fecha"], "tipo": clave, "concepto": e["concepto"],
                                "monto_usd": round(usd, 2), "es_estimado": es_estimado,
                                "descripcion": descripcion})

    resultado = list(serie.values())
    for f in resultado:
        for k in ("interes_usd", "amortizacion_usd", "otros_usd", "alquileres_usd", "dividendos_usd"):
            f[k] = round(f[k], 2)
        f["total_usd"] = round(f["interes_usd"] + f["amortizacion_usd"] + f["otros_usd"]
                               + f["alquileres_usd"] + f["dividendos_usd"], 2)

    promedio = sum(f["total_usd"] for f in resultado) / len(resultado) if resultado else 0.0
    umbral = piso_usd if piso_usd is not None else promedio * umbral_pct_promedio
    for f in resultado:
        f["mes_flojo"] = f["total_usd"] < umbral
    return {"serie": resultado, "promedio_usd": round(promedio, 2), "umbral_usd": round(umbral, 2)}
