"""
PWOS -- Feed de cotizaciones (Tarea 6, sesion 2026-07-14)

Logica de matching, sin tocar la base. `importador_precios.py` reusa estas
funciones para insertar de verdad.

Fuentes:
- dolarapi.com/v1/dolares/bolsa -> USD MEP (unidad de cuenta, ADR-001).
  Si esta consulta falla, todo el proceso se frena: no tiene sentido
  cargar precios de activos sin el MEP del dia para convertir.
- data912.com, 5 paneles BYMA (sin auth; cache-control max-age=30 medido el 2026-09-24 -- antes se
  suponia ~2hs via Cloudflare):
  arg_stocks, arg_cedears, arg_bonds, arg_corp, arg_notes.

Convencion de precio de MEP (confirmada con el usuario): se usa el campo
`venta` de dolarapi -- lo que pagarias para comprar USD MEP hoy -- no
`compra` ni un promedio.

Solo se busca precio para activos VIGENTES (vista_posiciones, cantidad
!= 0) -- pedir precio de algo fuera de cartera no aporta nada y gasta
llamadas de una API publica.

El ticker en `activos.ticker` ya es el canonico (convencion D del usuario
para especies O/D/C unificadas) y matchea exacto contra el `symbol` de
data912 -- sin logica de sufijo.

Moneda del precio de data912 para bonos/ONs (bug critico 2026-07-14):
data912 devuelve el precio de mercado de cada instrumento, pero segun
la especie en la que ese ticker puntual cotiza, ese precio puede venir
en ARS o en USD directo:
- Especie O (pesos) y C (dolar cable): el precio de mercado es el
  equivalente en pesos -- para pasarlo a USD hay que dividir por MEP.
- Especie D (dolar MEP): en algunos casos el instrumento que lista
  data912 bajo ese ticker YA cotiza en USD directo -- dividir de nuevo
  por MEP lo subvalua ~15-20x.
El sufijo del ticker NO alcanza para decidir esto de forma automatica:
hay excepciones confirmadas (ej. un bono provincial que cotiza en
pesos pese al sufijo D, con otra especie para la version en dolares). Por eso
la clasificacion se guarda como dato explicito por ticker en
`MONEDA_PRECIO_DATA912` (mismo criterio que `ESPECIE_CANONICA` en
`armar_movimientos.py`: mapa a mano, no heuristica), verificada de
forma empirica comparando el orden de magnitud del precio contra el
rango esperado (~90-110 = ya esta en USD; miles/decenas de miles = esta
en el equivalente en pesos). Un ticker de tipo bono/ON que no este en
este mapa NO se inserta -- se marca 'requiere_clasificacion_manual' y
se frena para que el usuario lo revise, en vez de asumir una clasificacion.
"""

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import time

import requests

import configuracion

DB_PATH = Path(__file__).parent / "pwos.db"

MEP_URL = "https://dolarapi.com/v1/dolares/bolsa"
CCL_URL = "https://dolarapi.com/v1/dolares/contadoconliqui"
DATA912_BASE = "https://data912.com/live"

# Que paneles de data912 se consultan para cada tipo de activo. LECAP
# aparece repartido entre dos paneles (no asumir uno solo).
PANELES_POR_TIPO = {
    "accion": ["arg_stocks"],
    "cedear": ["arg_cedears"],
    "bono_soberano": ["arg_bonds"],
    "bono_provincial": ["arg_bonds"],
    "bono_cer": ["arg_bonds"],
    "on": ["arg_corp"],
    "lecap": ["arg_bonds", "arg_notes"],
}

TODOS_LOS_PANELES = ["arg_stocks", "arg_cedears", "arg_bonds", "arg_corp", "arg_notes"]

# Tipos donde la especie O/D/C es relevante para la moneda del precio de
# mercado (ver docstring del modulo). LECAP queda afuera a proposito:
# son siempre instrumentos en pesos, no tienen variante dolar posible.
TIPOS_CON_ESPECIE_MONEDA = {"bono_soberano", "bono_provincial", "bono_cer", "on"}

# Clasificacion empirica por ticker (sesion 2026-07-14), verificada
# comparando cada precio de data912 contra el rango esperado en dolares
# (~90-110 = precio ya en USD; miles/decenas de miles = precio en su
# equivalente en pesos). Completar esta entrada a mano cuando aparezca
# un ticker nuevo de estos tipos -- ver regla en `construir_reporte`.
MONEDA_PRECIO_DATA912 = configuracion.get("precios_data912", "moneda_precio", default={})

# El ticker canonico de un dollar-linked puede no llevar sufijo D (a diferencia de la
# convencion del resto del catalogo, ver ESPECIE_CANONICA en
# armar_movimientos.py -- mismo tipo de desvio, no corregido de raiz
# todavia) pero la cotizacion correcta viene de la especie D de data912.
# Mapeo puntual solo para la busqueda en los paneles, sin renombrar el
# ticker interno (evita mezclar esto con el pendiente mas amplio de
# ESPECIE_CANONICA, que afecta otros 8 tickers y sigue sin decidir).
TICKER_OVERRIDE_DATA912 = configuracion.get("precios_data912", "ticker_override", default={})


def _get(url: str) -> requests.Response:
    """GET con reintentos (3 intentos, timeout 10s/20s/30s, espera 2s/4s
    entre ellos). Encontrado 2026-09-23 (ver ESTADO_PWOS.md): el job diario
    de n8n fallo el 09-21 y 09-22 por un unico `Read timed out (10s)` de
    data912.com, sin reintento -- y como data912 solo da el precio de HOY,
    un dia perdido nunca se recupera solo. Si los 3 intentos fallan, la
    excepcion del ultimo se propaga igual que antes."""
    ultimo = None
    for intento in range(3):
        try:
            r = requests.get(url, timeout=10 * (intento + 1))
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            ultimo = e
            if intento < 2:
                time.sleep(2 * (intento + 1))
    raise ultimo


def obtener_mep() -> dict:
    """Trae USD MEP de dolarapi. Deja que la excepcion se propague:
    el llamador decide frenar todo el proceso si esto falla."""
    r = _get(MEP_URL)
    data = r.json()
    return {
        "venta": data["venta"],
        "compra": data["compra"],
        "fecha_actualizacion": data["fechaActualizacion"],
    }


def obtener_ccl() -> dict:
    """Trae USD CCL (contado con liqui) de dolarapi. Usado para valorizar
    CEDEARs (ver valorizacion.py, TIPOS_CCL) - confirmado 2026-07-29
    comparando contra el Excel del usuario: convertir CEDEARs con MEP los
    sobreestimaba sistematicamente (~USD 130 en conjunto, TODOS los
    CEDEARs de la cartera por encima del valor real, nunca al reves);
    con CCL el error cae a ruido de redondeo/horario (<USD 10 en
    conjunto). Deja que la excepcion se propague, mismo criterio que
    obtener_mep()."""
    r = _get(CCL_URL)
    data = r.json()
    return {
        "venta": data["venta"],
        "compra": data["compra"],
        "fecha_actualizacion": data["fechaActualizacion"],
    }


def obtener_panel(nombre: str) -> dict[str, float]:
    """Devuelve {symbol: precio_cierre} para un panel de data912."""
    r = _get(f"{DATA912_BASE}/{nombre}")
    return {item["symbol"]: item["c"] for item in r.json()}


def obtener_todos_los_paneles() -> dict[str, dict[str, float]]:
    return {nombre: obtener_panel(nombre) for nombre in TODOS_LOS_PANELES}


def obtener_activos_vigentes(con) -> list[tuple[int, str, str]]:
    """Activos con posicion vigente (cantidad != 0) en algun contenedor,
    deduplicados por activo (la vista repite un activo si esta en mas
    de un broker)."""
    return con.execute(
        "SELECT DISTINCT activo_id, ticker, tipo_activo FROM vista_posiciones "
        "ORDER BY tipo_activo, ticker;"
    ).fetchall()


def _clasificar_moneda_precio(ticker: str, tipo: str) -> str | None:
    """Moneda en la que viene el precio de data912 para este ticker.
    None si el tipo requiere clasificacion (bono/ON) y el ticker no esta
    en el mapa explicito -- nunca se adivina por sufijo."""
    if tipo not in TIPOS_CON_ESPECIE_MONEDA:
        return "ARS"  # accion/cedear/lecap: sin ambiguedad conocida
    return MONEDA_PRECIO_DATA912.get(ticker)


def construir_reporte(con, paneles: dict[str, dict[str, float]]) -> list[dict]:
    """Para cada activo vigente, busca su ticker en los paneles que le
    corresponden segun su tipo. Devuelve una fila por activo con el
    resultado del matching (matched / no_match / ambiguo /
    requiere_clasificacion_manual)."""
    filas = []
    for activo_id, ticker, tipo in obtener_activos_vigentes(con):
        ticker_panel = TICKER_OVERRIDE_DATA912.get(ticker, ticker)
        paneles_candidatos = PANELES_POR_TIPO.get(tipo, [])
        encontrados = [
            (nombre_panel, paneles[nombre_panel][ticker_panel])
            for nombre_panel in paneles_candidatos
            if ticker_panel in paneles[nombre_panel]
        ]

        if len(encontrados) == 0:
            filas.append({
                "activo_id": activo_id, "ticker": ticker, "tipo": tipo,
                "panel": None, "precio": None, "moneda_precio": None,
                "estado": "no_match",
            })
            continue

        if len(encontrados) == 1:
            panel, precio = encontrados[0]
        else:
            # Mismo ticker en mas de un panel candidato (caso LECAP).
            # Si coinciden en precio no hay ambiguedad real; si difieren,
            # no elegir a ciegas.
            precios_distintos = {p for _, p in encontrados}
            if len(precios_distintos) != 1:
                filas.append({
                    "activo_id": activo_id, "ticker": ticker, "tipo": tipo,
                    "panel": encontrados, "precio": None, "moneda_precio": None,
                    "estado": "ambiguo",
                })
                continue
            panel, precio = encontrados[0]

        moneda_precio = _clasificar_moneda_precio(ticker, tipo)
        if moneda_precio is None:
            filas.append({
                "activo_id": activo_id, "ticker": ticker, "tipo": tipo,
                "panel": panel, "precio": precio, "moneda_precio": None,
                "estado": "requiere_clasificacion_manual",
            })
            continue

        filas.append({
            "activo_id": activo_id, "ticker": ticker, "tipo": tipo,
            "panel": panel, "precio": precio, "moneda_precio": moneda_precio,
            "estado": "matched",
        })
    return filas


def imprimir_reporte(reporte: list[dict], mep: dict) -> None:
    print("=" * 70)
    print(f"  USD MEP (dolarapi, venta): {mep['venta']}  "
          f"(actualizado: {mep['fecha_actualizacion']})")
    print("=" * 70)

    print(f"\n{'TICKER':<10} {'TIPO':<18} {'PANEL':<14} {'PRECIO':>12} {'MON':<5} ESTADO")
    print("-" * 70)
    for f in reporte:
        panel_str = f["panel"] if isinstance(f["panel"], str) else "-"
        precio_str = f"{f['precio']:.2f}" if f["precio"] is not None else "-"
        mon_str = f["moneda_precio"] or "-"
        print(f"{f['ticker']:<10} {f['tipo']:<18} {panel_str:<14} "
              f"{precio_str:>12} {mon_str:<5} {f['estado']}")

    sin_match = [f for f in reporte if f["estado"] == "no_match"]
    ambiguos = [f for f in reporte if f["estado"] == "ambiguo"]
    sin_clasificar = [f for f in reporte if f["estado"] == "requiere_clasificacion_manual"]

    print(f"\nTotal activos vigentes: {len(reporte)}")
    print(f"Matcheados: {len([f for f in reporte if f['estado'] == 'matched'])}")

    if sin_match:
        print(f"\nSIN PRECIO DISPONIBLE ({len(sin_match)}):")
        for f in sin_match:
            print(f"  - {f['ticker']} ({f['tipo']}) -- no aparece en ningun panel esperado")

    if ambiguos:
        print(f"\nAMBIGUOS, no se cargan sin revision manual ({len(ambiguos)}):")
        for f in ambiguos:
            print(f"  - {f['ticker']} ({f['tipo']}): {f['panel']}")

    if sin_clasificar:
        print(f"\nREQUIEREN CLASIFICACION MANUAL DE MONEDA, no se cargan ({len(sin_clasificar)}):")
        for f in sin_clasificar:
            print(f"  - {f['ticker']} ({f['tipo']}): precio {f['precio']} en {f['panel']} -- "
                  f"agregar a precios_data912.moneda_precio en la configuracion tras confirmarlo")


if __name__ == "__main__":
    con = sqlite3.connect(DB_PATH)

    try:
        mep = obtener_mep()
    except Exception as e:
        print(f"ABORTADO: no se pudo obtener USD MEP de dolarapi.com ({e}).")
        print("No tiene sentido seguir sin la cotizacion MEP del dia.")
        raise SystemExit(1)

    paneles = obtener_todos_los_paneles()
    reporte = construir_reporte(con, paneles)
    imprimir_reporte(reporte, mep)

    con.close()
