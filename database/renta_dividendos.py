"""
PWOS -- Renta de bonos y dividendos de acciones/CEDEARs YA COBRADOS
(2026-08-26), complemento del panel de Flujo de pagos (que solo muestra
lo PROYECTADO) y del panel de Ganancia/perdida (seccion "Renta
variable").

Cada movimiento `tipo='renta'` pasa por 3 filtros antes de contar como
"cobrado":

1. SOLO ingresos reales (`cuenta_destino_id IS NOT NULL`). Varios brokers
   generan una fila "espejo" en pesos por cada cobro real en USD/USD_CCL
   (la retencion/mirror de conversion, `cuenta_origen_id` seteado,
   `cuenta_destino_id` NULL -- NO es un segundo ingreso). El caso mas
   dificil de ver: un dividendo de un CEDEAR extranjero puede venir con
   `total` NEGATIVO en el export crudo (retencion en origen, sin credito
   local real), a diferencia de una accion local, que si tiene un credito
   ARS real y positivo. Filtrar por direccion real de la plata resuelve
   todos los casos con una sola regla, sin texto por broker.

2. Interes de CAUCION -- excluido, no es rendimiento de la inversion,
   es interes de un prestamo colateralizado de corto plazo. Patron de
   texto por broker en la configuracion (`patrones_caucion`).

3. AMORTIZACION -- excluida, es capital devuelto, no rendimiento
   (decision del usuario 2026-08-26, mismo criterio que ya usa el panel de
   ganancia/perdida). Dos vias, ninguna nueva:
     a. Estructural: cualquier `renta` con una fila en
        `operaciones_activos` vinculada es un cierre de nominal (100%
        amortizado) -- mismo mecanismo que ya usa `vista_posiciones`
        para restar la tenencia. Cubre los cierres completos de todos
        los brokers sin texto.
     b. Textual, para amortizaciones PARCIALES que no cierran la
        posicion (no tienen fila en operaciones_activos): reusa
        `dashboard.PATRONES_AMORTIZACION_TEXTO` +
        `dashboard.AMORTIZACIONES_CONFIRMADAS_MANUAL`, la misma fuente
        de verdad que ya usa el panel de ganancia/perdida -- no se
        duplica el criterio en un segundo lugar.

Lo que sobrevive a los 3 filtros se separa en DIVIDENDO (ticker de tipo
accion/cedear) o RENTA DE BONO (bono_soberano/provincial/cer/on/lecap),
via el clasificador de texto del formato de cada broker (`formato_renta`
en la configuracion). Cuando el ticker no es identificable en el dato
crudo (un export que no lo trae) el monto entra en un bucket
"sin_atribuir" APARTE -- nunca se descarta ni se mezcla a ciegas con lo
atribuido. El total de renta de un broker asi sigue siendo correcto: se
reporta agregado por broker, no por ticker.
"""
import re
from datetime import date

import configuracion
import dashboard  # PATRONES_AMORTIZACION_TEXTO, AMORTIZACIONES_CONFIRMADAS_MANUAL
from valorizacion import parte_propia, pct_de, pct_propiedad_por_cuenta

# Brokers del usuario (configuracion): formato de su export de renta y
# textos que identifican el interes de caucion.
_BROKERS = configuracion.brokers()


def _es_caucion(broker: str, texto: str) -> bool:
    """Interes de caucion (no es rendimiento genuino de la inversion): algun
    texto de `patrones_caucion` del broker en la configuracion, sobre la
    descripcion ya en minusculas."""
    return any(p in texto for p in _BROKERS.get(broker, {}).get("patrones_caucion", []))


def _clasificar_generico(descripcion: str):
    """Formato neutro, para un broker sin export propio (la demo): el texto
    empieza con "Dividendo KO", "Renta GD35", "Cupon GD35", "Interes" o
    "Amortizacion GD35"."""
    m = re.match(r"^(Dividendo|Renta|Cup[oó]n|Inter[eé]s|Amortizaci[oó]n)\b\s*([A-Z0-9.]+)?", descripcion)
    if not m:
        return "no_clasificado", None
    tipo, ticker = m.group(1).lower(), m.group(2)
    if tipo.startswith("amortiz"):
        return "amortizacion", ticker
    if tipo == "dividendo":
        return ("dividendo", ticker) if ticker else ("dividendo_sin_atribuir", None)
    return "renta", ticker


# formato_renta (configuracion de cada broker) -> clasificador del texto. El
# publico trae el formato generico; los formatos del export de cada broker
# concreto viven en un modulo local opcional (formatos_renta_privados.py, que
# no se publica) y se suman aca si esta.
_CLASIFICADORES = {
    "generico": lambda d, mid, cfg: _clasificar_generico(d),
}
try:
    import formatos_renta_privados
    _CLASIFICADORES.update(formatos_renta_privados.CLASIFICADORES)
except ImportError:
    pass


def _clasificador(broker: str):
    cfg = _BROKERS.get(broker)
    if cfg is None or cfg.get("formato_renta") not in _CLASIFICADORES:
        return None
    fn = _CLASIFICADORES[cfg["formato_renta"]]
    return lambda d, mid: fn(d, mid, cfg)


def _fx_historico_o_actual(con, moneda_fx: str, fecha: str, mep: float, ccl: float) -> float:
    """FX de la fecha del cobro; si no hay dato historico cacheado, usa
    el de hoy como ultimo recurso SOLO para no tirar el numero entero
    (marca `aproximado=True` para que el llamador lo distinga -- mismo
    espiritu de transparencia que el resto del codebase, pero renta
    cobrada tolera esta aproximacion mejor que "invertido" porque es un
    monto ya generado, no una decision de inversion a explicar)."""
    fx = dashboard.precio_fx_historico(con, moneda_fx, fecha)
    if fx is not None:
        return fx, False
    return (ccl if moneda_fx == "USD_CCL" else mep), True


def _movimientos_candidatos(con, anio: int):
    """`monto` ya en la parte propia (contenedor compartido, ADR-032)."""
    pcts = pct_propiedad_por_cuenta(con)
    filas = con.execute("""
        SELECT m.id, co.nombre, m.descripcion, m.fecha, m.moneda, m.monto, c.id
        FROM movimientos m
        JOIN cuentas c ON c.id = m.cuenta_destino_id
        JOIN contenedores co ON co.id = c.contenedor_id
        WHERE m.tipo = 'renta' AND m.naturaleza = 'efectivo'
          AND strftime('%Y', m.fecha) = ?
        ORDER BY co.nombre, m.fecha;
    """, (str(anio),)).fetchall()
    return [(*f[:5], parte_propia(f[5], pct_de(pcts, f[6]))) for f in filas]


def _ids_cierre_nominal(con) -> set:
    """movimiento_id de cualquier 'renta' con fila en operaciones_activos
    -- cierre de nominal (amortizacion 100%), ver docstring del modulo."""
    return {
        r[0] for r in con.execute(
            "SELECT m.id FROM movimientos m "
            "JOIN operaciones_activos oa ON oa.movimiento_id = m.id "
            "WHERE m.tipo = 'renta';"
        ).fetchall()
    }


def _es_amortizacion_textual(broker: str, ticker: str | None, descripcion: str) -> bool:
    if ticker is None:
        return False
    for patron in dashboard.PATRONES_AMORTIZACION_TEXTO:
        like = patron.format(ticker=ticker)
        # traducir el patron SQL LIKE (%) a un chequeo simple de substrings
        partes = [p for p in like.split("%") if p]
        if all(p in descripcion for p in partes):
            return True
    return False


def calcular_renta_y_dividendos_cobrados(con, mep: float, ccl: float, anio: int | None = None) -> dict:
    """Devuelve {"anio", "renta_bonos": {...}, "dividendos": {...}} con
    los montos YA COBRADOS en el año (no proyectados) en USD, separando
    renta de bonos (excluye caucion y amortizacion) de dividendos de
    acciones/CEDEARs en efectivo (excluye lo en_especie, tratado aparte
    via ADR-010 -- nunca llega con naturaleza='efectivo')."""
    anio = anio or configuracion.hoy().year
    ids_cierre_nominal = _ids_cierre_nominal(con)
    tipo_por_ticker = dict(con.execute("SELECT ticker, tipo FROM activos;").fetchall())

    renta_por_broker: dict[str, float] = {}
    renta_aproximada = False
    dividendos_por_ticker: dict[str, float] = {}
    dividendos_sin_atribuir_por_broker: dict[str, float] = {}
    dividendos_aproximado = False
    no_clasificados = []

    for movimiento_id, broker, descripcion, fecha, moneda, monto in _movimientos_candidatos(con, anio):
        texto_normalizado = descripcion.lower()
        if _es_caucion(broker, texto_normalizado):
            continue
        if movimiento_id in ids_cierre_nominal:
            continue  # amortizacion 100%, capital no rendimiento

        clasificador = _clasificador(broker)
        if clasificador is None:
            no_clasificados.append((broker, descripcion, monto, moneda))
            continue
        categoria, ticker = clasificador(descripcion, movimiento_id)

        if categoria == "amortizacion" or _es_amortizacion_textual(broker, ticker, descripcion):
            continue  # amortizacion parcial (texto), capital no rendimiento

        if moneda in ("USD", "USD_CCL"):
            monto_usd, aproximado = monto, False
        else:
            moneda_fx = "USD_CCL" if tipo_por_ticker.get(ticker) in dashboard.TIPOS_CCL else "USD_MEP"
            fx, aproximado = _fx_historico_o_actual(con, moneda_fx, fecha, mep, ccl)
            monto_usd = monto / fx

        if categoria == "dividendo":
            dividendos_por_ticker[ticker] = dividendos_por_ticker.get(ticker, 0.0) + monto_usd
            dividendos_aproximado = dividendos_aproximado or aproximado
        elif categoria == "dividendo_sin_atribuir":
            dividendos_sin_atribuir_por_broker[broker] = (
                dividendos_sin_atribuir_por_broker.get(broker, 0.0) + monto_usd
            )
            dividendos_aproximado = dividendos_aproximado or aproximado
        elif categoria == "renta":
            renta_por_broker[broker] = renta_por_broker.get(broker, 0.0) + monto_usd
            renta_aproximada = renta_aproximada or aproximado
        else:
            no_clasificados.append((broker, descripcion, monto, moneda))

    return {
        "anio": anio,
        "renta_bonos": {
            "por_broker": [
                {"contenedor": k, "monto_usd": round(v, 2)} for k, v in sorted(renta_por_broker.items())
            ],
            "total_usd": round(sum(renta_por_broker.values()), 2),
            "aproximado": renta_aproximada,
        },
        "dividendos": {
            "por_ticker": [
                {"ticker": k, "monto_usd": round(v, 2)} for k, v in sorted(dividendos_por_ticker.items())
            ],
            "sin_atribuir": [
                {"contenedor": k, "monto_usd": round(v, 2)}
                for k, v in sorted(dividendos_sin_atribuir_por_broker.items())
            ],
            "total_usd": round(
                sum(dividendos_por_ticker.values()) + sum(dividendos_sin_atribuir_por_broker.values()), 2
            ),
            "aproximado": dividendos_aproximado,
        },
        "no_clasificados": [
            {"contenedor": b, "descripcion": d, "monto": m, "moneda": mo}
            for b, d, m, mo in no_clasificados
        ],
    }
