"""
Snapshot de solo lectura de toda la cartera (financiera + real estate),
para que otra herramienta lo consulte 1 vez por dia. Solo reordena datos
que ya calcula el resto del sistema (valorizar_saldos_cuenta,
valorizar_posiciones, listar_valor_inmuebles, flujo_inmueble) -- no
reimplementa ninguna valuacion ni escribe nada en la base.

Sin pasivos (decision explicita del usuario): es patrimonio BRUTO de activos.
Cuadra con /api/resumen asi: patrimonio_bruto = total_efectivo +
total_inversiones + total_inmuebles (= patrimonio_total + total_pasivos).

`exposicion` (2 ejes independientes, sobre patrimonio bruto):
  - pct_usd/pct_ars: moneda ECONOMICA. Renta fija por
    bonos_referencia.moneda_pago (USD y USD_LINKED -> USD, ARS -> ARS);
    CEDEAR -> USD; accion argentina -> ARS; efectivo por la moneda real
    de la cuenta; real estate -> USD.
  - pct_argentina/pct_exterior: riesgo de custodia/soberano, no moneda.
    CEDEAR -> exterior; todo lo demas (bonos, acciones AR, efectivo en
    bancos/brokers locales, real estate) -> Argentina.
Lo que no se pueda clasificar (tipo nuevo, bono sin moneda_pago) NO se
adivina: suma a `no_clasificado_usd` y queda fuera de los porcentajes.
"""
from datetime import date

from dashboard import obtener_precio_fx, valorizar_saldos_cuenta, valorizar_posiciones
from valorizacion import convertir_efectivo_a_usd, pct_de, pct_propiedad_por_cuenta, RENTA_FIJA
from flujo_inmueble import flujo_inmueble
from propiedades import listar_propiedades
from valuaciones import valuacion_vigente
from valorizacion import solo_cierre
import configuracion

# Un fondo inmobiliario (`fondo_re`): su valor ya esta en efectivo (su
# propia cuenta, ver migrar_v228_valuaciones_pasivos.py) y se reporta en
# real_estate, no en liquidez, para no contarlo en 2 secciones.
TIPO_FONDO_RE = "fondo_re"


def _exposicion(con, mep, contenedores_fondo_re, posiciones, valor_re_usd) -> dict:
    usd = ars = arg = ext = sin_clasificar = 0.0

    # Efectivo: moneda real de la cuenta; siempre en custodia local. En la
    # parte propia (contenedores compartidos, ADR-032).
    pcts = pct_propiedad_por_cuenta(con)
    for cid, _alias, moneda, _estado, contenedor, saldo in con.execute("SELECT * FROM vista_saldos_cuenta;"):
        if moneda == "CARTERA" or contenedor in contenedores_fondo_re:
            continue
        v = convertir_efectivo_a_usd(saldo, moneda, mep, pct_de(pcts, cid))
        if moneda in ("USD", "USD_CCL"):
            usd += v
        else:
            ars += v
        arg += v

    # Posiciones
    moneda_pago = dict(con.execute(
        "SELECT a.ticker, b.moneda_pago FROM bonos_referencia b JOIN activos a ON a.id = b.activo_id"
    ).fetchall())
    for p in posiciones:
        v = p["valor_usd"]
        if v is None:
            continue
        if p["tipo"] == "cedear":
            usd += v
            ext += v
        elif p["tipo"] == "accion":
            ars += v
            arg += v
        elif p["tipo"] in RENTA_FIJA and moneda_pago.get(p["ticker"]) in ("USD", "USD_LINKED", "ARS"):
            if moneda_pago[p["ticker"]] == "ARS":
                ars += v
            else:
                usd += v
            arg += v
        else:
            sin_clasificar += v

    # Real estate (propiedades fisicas y fondo_re): USD, Argentina.
    usd += valor_re_usd
    arg += valor_re_usd

    total_moneda = usd + ars
    total_pais = arg + ext
    pct_usd = round(usd / total_moneda * 100, 1) if total_moneda else None
    pct_arg = round(arg / total_pais * 100, 1) if total_pais else None
    return {
        "pct_usd": pct_usd,
        "pct_ars": round(100 - pct_usd, 1) if pct_usd is not None else None,
        "pct_argentina": pct_arg,
        "pct_exterior": round(100 - pct_arg, 1) if pct_arg is not None else None,
        "no_clasificado_usd": round(sin_clasificar, 2),
    }


@solo_cierre()  # historia / escribe en la base: siempre con el cierre (ADR-033)
def armar_snapshot(con) -> dict:
    mep = obtener_precio_fx(con, "USD_MEP")
    ccl = obtener_precio_fx(con, "USD_CCL")

    tipo_por_contenedor = dict(con.execute("SELECT nombre, tipo FROM contenedores").fetchall())
    saldos = valorizar_saldos_cuenta(con, mep)
    posiciones = valorizar_posiciones(con, mep, ccl)

    props = listar_propiedades(con)
    contenedores_fondo_re = {p["alias"] for p in props if p["tipo"] == TIPO_FONDO_RE}

    # --- liquidez: todo el efectivo (bancos, billeteras, brokers), por
    # contenedor, salvo el de los fondos de real estate ---
    liquidez_por_contenedor: dict[str, float] = {}
    valor_fondo_re: dict[str, float] = {}
    for f in saldos:
        c = f["contenedor"]
        if c in contenedores_fondo_re:
            valor_fondo_re[c] = valor_fondo_re.get(c, 0.0) + f["valor_usd"]
        else:
            liquidez_por_contenedor[c] = liquidez_por_contenedor.get(c, 0.0) + f["valor_usd"]
    liquidez_detalle = [
        {"contenedor": c, "tipo": tipo_por_contenedor.get(c), "valor_usd": round(v, 2)}
        for c, v in liquidez_por_contenedor.items() if abs(v) > 1e-6
    ]
    liquidez_detalle.sort(key=lambda d: -d["valor_usd"])
    total_liquidez = sum(liquidez_por_contenedor.values())

    # --- mercado financiero: posiciones vigentes agrupadas por ticker ---
    por_ticker: dict[str, dict] = {}
    for p in posiciones:
        fila = por_ticker.setdefault(
            p["ticker"], {"ticker": p["ticker"], "tipo": p["tipo"], "cantidad": 0.0, "valor_usd": 0.0, "sin_precio": False}
        )
        fila["cantidad"] += p["cantidad"]
        if p["valor_usd"] is None:
            fila["sin_precio"] = True
        else:
            fila["valor_usd"] += p["valor_usd"]

    tesis_por_ticker = {}
    for ticker, motivo, riesgo, horizonte, objetivo, criterio in con.execute(
        """
        SELECT a.ticker, t.motivo, t.riesgo, t.horizonte, t.precio_objetivo, t.criterio_venta
        FROM tesis_inversion t JOIN activos a ON a.id = t.activo_id
        """
    ):
        tesis_por_ticker[ticker] = {
            "motivo": motivo, "riesgo": riesgo, "horizonte": horizonte,
            "precio_objetivo": objetivo, "criterio_venta": criterio,
        }

    total_financiero = sum(f["valor_usd"] for f in por_ticker.values())

    # --- real estate ---
    propiedades_out = []
    for p in props:
        if p["tipo"] == TIPO_FONDO_RE:
            valor_neto = valor_fondo_re.get(p["alias"], 0.0)
            tir = None
        else:
            vigente = valuacion_vigente(con, p["id"])
            if vigente is None:
                continue
            valor_neto = vigente["valor_usd_propio"]
            tir = flujo_inmueble(con, p["id"])["tir_pct"]
        propiedades_out.append({
            "nombre": p["alias"],
            "tipo": p["tipo"],
            "pct_propiedad": p["pct_propiedad"],
            "valor_neto_usd": round(valor_neto, 2),
            "tir_anual_pct": round(tir, 2) if tir is not None else None,
        })
    total_re = sum(x["valor_neto_usd"] for x in propiedades_out)

    patrimonio_bruto = total_liquidez + total_financiero + total_re

    posiciones_out = []
    for f in sorted(por_ticker.values(), key=lambda f: -f["valor_usd"]):
        sin_precio = f["sin_precio"]
        posiciones_out.append({
            "ticker": f["ticker"],
            "tipo": f["tipo"],
            "cantidad": f["cantidad"],
            "valor_usd": None if sin_precio else round(f["valor_usd"], 2),
            "peso_pct_patrimonio": (
                None if sin_precio or not patrimonio_bruto
                else round(f["valor_usd"] / patrimonio_bruto * 100, 1)
            ),
            "tesis": tesis_por_ticker.get(f["ticker"]),
        })

    return {
        "fecha_snapshot": configuracion.hoy().isoformat(),
        "patrimonio_bruto_usd": round(patrimonio_bruto, 2),
        "liquidez": {"total_usd": round(total_liquidez, 2), "detalle": liquidez_detalle},
        "mercado_financiero": {"total_usd": round(total_financiero, 2), "posiciones": posiciones_out},
        "real_estate": {"total_usd": round(total_re, 2), "propiedades": propiedades_out},
        "exposicion": _exposicion(con, mep, contenedores_fondo_re, posiciones, total_re),
    }
