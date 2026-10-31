"""
PWOS -- flujo de caja + TIR/VPN de una propiedad de Real Estate
(Real Estate -- valuacion/deuda/TIR, 2026-09-18).

Junta, para una propiedad puntual:
  - `propiedades.inversion_inicial_usd` (2026-09-19), si no es NULL:
    flujo negativo en `fecha_alta` -- capital propio puesto para
    adquirir/construir la propiedad (None a proposito si esta 100%
    financiada por `pasivos` -- ver
    propiedades.py::actualizar_inversion_inicial).
  - `alquileres` cobrados (flujo positivo, ya neto segun pct_propiedad).
  - `gastos_propiedad` (mantenimiento, impuesto -- 2026-09-19): flujo
    negativo, ya neto segun pct_propiedad (mismo patron que pago_pasivo).
  - `pagos_pasivo` del pasivo vinculado a esa propiedad, si existe
    (flujo negativo).
  - un flujo sintetico final EN LA FECHA DE HOY = ultima valuacion x
    pct_propiedad MENOS el saldo VIGENTE del pasivo vinculado (positivo
    neto) -- el "valor de rescate" real si el usuario vendiera hoy y
    liquidara la deuda pendiente, no el valor bruto del activo.

    Por que neto y no bruto (encontrado armando este modulo, 2026-09-18):
    con el valor BRUTO, la serie queda formada por pagos de deuda
    chicos (negativos) contra una valuacion terminal grande (positiva)
    que casi nunca tiene una raiz real en el rango [-99%, 1000%] que
    usa `xirr` -- el "retorno" resultante no tiene sentido economico
    (haria falta un descuento mas extremo que -99% anual para que un
    pago chico "empate" contra una valuacion 25 veces mayor). Neteando el
    saldo pendiente, el flujo terminal representa la EQUITY real del
    usuario (lo que le queda despues de pagar lo que debe), que sube con
    cada pago (correcto: pagar deuda construye equity) sin duplicar
    nada -- cada `pago_pasivo` ya cuenta aparte, en su propia fecha,
    como salida real de caja; el saldo NETEADO en el terminal
    es un snapshot de HOY, no una segunda vez la suma de esos pagos.

Se recalcula en cada llamada, sin nada guardado -- cada alquiler/pago/
valuacion nueva lo actualiza solo, sin tocar codigo (criterio de
aceptacion del ticket). Reusa el MISMO motor de TIR que renta fija
(`tir_bonos.xirr`/`van`) -- no se reimplementa el descuento en un
segundo lugar del proyecto.

Restringido a `propiedades.tipo = 'activo_fisico'` -- para un `fondo_re`
(fondo inmobiliario) la renta se REINVIERTE
dentro de la misma cuenta que YA es el saldo/valor del fondo: si se
cargara su alquiler como flujo positivo Y ADEMAS se usara su saldo
actual (que ya incluye esa renta acumulada) como flujo terminal, la
misma plata se contaria 2 veces dentro del calculo de TIR. El retorno
de un fondo con capital bloqueado hasta un vencimiento es un problema
distinto (TIR de una inversion a plazo con valor de rescate unico, no
de un activo con renta+deuda) -- fuera de alcance de este ticket a
proposito, no se fuerza mal aca (ver `PropiedadNoAplicaError`).
"""

from datetime import date

from tir_bonos import xirr, van
import configuracion

# Tasa de descuento por defecto para el VPN y su fuente, de la configuracion
# del usuario (real_estate.*): una referencia de rentabilidad BRUTA de alquiler
# residencial en USD de la plaza de sus propiedades -- aproximacion, no un dato
# de cada propiedad. `TASA_DESCUENTO_FUENTE` viaja en la respuesta de
# flujo_inmueble() SOLO cuando se usa esta tasa por default: si el caller pasa
# su propia `tasa_descuento`, la fuente ya no aplica y queda en None.
TASA_DESCUENTO_DEFAULT = configuracion.get("real_estate", "tasa_descuento", default=0.06)
TASA_DESCUENTO_FUENTE = configuracion.get("real_estate", "tasa_descuento_fuente", default=None)


class PropiedadNoAplicaError(ValueError):
    """Se pidio el flujo de una propiedad que no es `activo_fisico`
    (un `fondo_re`) -- ver docstring del modulo."""


def flujo_inmueble(con, propiedad_id: int, tasa_descuento: float = TASA_DESCUENTO_DEFAULT) -> dict:
    propiedad = con.execute(
        "SELECT alias, tipo, pct_propiedad, fecha_alta, inversion_inicial_usd FROM propiedades WHERE id = ?;",
        (propiedad_id,),
    ).fetchone()
    if propiedad is None:
        raise ValueError(f"no existe la propiedad id={propiedad_id}")
    alias, tipo, pct_propiedad, fecha_alta, inversion_inicial_usd = propiedad
    if tipo != "activo_fisico":
        raise PropiedadNoAplicaError(
            f"'{alias}' es tipo={tipo!r} -- /api/flujo-inmueble solo aplica a propiedades "
            f"'activo_fisico' (ver docstring de flujo_inmueble.py: la renta de un fondo_re se "
            f"reinvierte dentro del mismo saldo que ya es su valuacion, contarla aparte la duplicaria)."
        )

    serie = []

    # Capital propio puesto para adquirir/construir la propiedad (Real
    # Estate -- inversion inicial + TIR/VPN, 2026-09-19, pedido del
    # usuario): sin este flujo, una propiedad pagada con plata PROPIA (no
    # financiada por `pasivos`) no tiene ningun egreso que descuente lo
    # que costo ponerla en pie -- la TIR queda inflada. None a proposito
    # para una propiedad 100% financiada por deuda (
    # cada `pago_pasivo` ya es alli la unica salida de caja real) -- no
    # se inventa un 0. Reusa `fecha_alta` (ya existente) como fecha de
    # este flujo, sin columna de fecha nueva.
    if inversion_inicial_usd is not None:
        serie.append({"fecha": fecha_alta, "tipo": "inversion_inicial", "monto_usd": round(-inversion_inicial_usd, 2)})

    for fecha_str, monto_usd in con.execute(
        "SELECT fecha_cobro, monto_usd FROM alquileres WHERE propiedad_id = ? ORDER BY fecha_cobro;",
        (propiedad_id,),
    ).fetchall():
        serie.append({"fecha": fecha_str, "tipo": "alquiler", "monto_usd": round(monto_usd, 2)})

    # Gastos reales (mantenimiento, impuesto -- Real Estate,
    # 2026-09-19): flujo
    # negativo, mismo patron que `pago_pasivo` -- ya neto segun
    # pct_propiedad (ver gastos_propiedad.py).
    for fecha_str, monto_usd in con.execute(
        "SELECT fecha, monto_usd FROM gastos_propiedad WHERE propiedad_id = ? ORDER BY fecha;",
        (propiedad_id,),
    ).fetchall():
        serie.append({"fecha": fecha_str, "tipo": "gasto_propiedad", "monto_usd": round(-monto_usd, 2)})

    saldo_pasivo_vigente = 0.0
    pasivo = con.execute(
        "SELECT id, monto_original_usd FROM pasivos WHERE propiedad_id = ?;", (propiedad_id,)
    ).fetchone()
    if pasivo is not None:
        pasivo_id, monto_original_usd = pasivo
        pagado_total = 0.0
        for fecha_str, monto_usd in con.execute(
            "SELECT fecha, monto_usd FROM pagos_pasivo WHERE pasivo_id = ? ORDER BY fecha;",
            (pasivo_id,),
        ).fetchall():
            serie.append({"fecha": fecha_str, "tipo": "pago_pasivo", "monto_usd": round(-monto_usd, 2)})
            pagado_total += monto_usd
        saldo_pasivo_vigente = round(monto_original_usd - pagado_total, 2)

    valuacion = con.execute(
        "SELECT fecha, valor_usd FROM valuaciones WHERE propiedad_id = ? ORDER BY fecha DESC, id DESC LIMIT 1;",
        (propiedad_id,),
    ).fetchone()
    valor_terminal_usd = None
    if valuacion is not None:
        _fecha_valuacion, valor_usd = valuacion
        # Neto del saldo pendiente del pasivo vinculado -- ver docstring
        # del modulo para el porque (equity real, no valor bruto).
        valor_terminal_usd = round(valor_usd * pct_propiedad / 100 - saldo_pasivo_vigente, 2)
        serie.append({
            "fecha": configuracion.hoy().isoformat(),
            "tipo": "valuacion_terminal",
            "monto_usd": valor_terminal_usd,
        })

    serie.sort(key=lambda f: f["fecha"])

    flujos_xirr = [(date.fromisoformat(f["fecha"]), f["monto_usd"]) for f in serie]
    tir = xirr(flujos_xirr) if len(flujos_xirr) >= 2 else None
    # VPN anclado a HOY (no a flujos_xirr[0][0], el default de van() --
    # ver tir_bonos.py): a diferencia de un bono, acá no hay una fecha
    # de "entrada" deliberada, y con alquiler historico cargado el
    # primer flujo puede ser de anios atras -- anclar ahi haria que el
    # VPN represente "valor en esa fecha vieja", no "valor hoy". Un
    # flujo pasado se capitaliza hacia adelante (formula estandar).
    vpn_usd = round(van(flujos_xirr, tasa_descuento, fecha0=configuracion.hoy()), 2) if flujos_xirr else None

    return {
        "propiedad_id": propiedad_id,
        "alias": alias,
        "pct_propiedad": pct_propiedad,
        "saldo_pasivo_vigente_usd": saldo_pasivo_vigente,
        "valor_terminal_usd": valor_terminal_usd,
        "serie": serie,
        "tasa_descuento_pct": round(tasa_descuento * 100, 2),
        "tasa_descuento_fuente": TASA_DESCUENTO_FUENTE if tasa_descuento == TASA_DESCUENTO_DEFAULT else None,
        "tir_pct": round(tir * 100, 2) if tir is not None else None,
        "vpn_usd": vpn_usd,
    }
