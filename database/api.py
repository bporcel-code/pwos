"""
PWOS -- Backend FastAPI del dashboard web (Tarea 8.4).

Elegido FastAPI + HTML/JS sobre Streamlit a proposito: el usuario esta
aprendiendo herramientas con mira a busqueda laboral, y el patron
API + cliente web es el mas reconocido en el mercado.

Mayormente solo lectura: ningun endpoint del dashboard en si inserta ni
modifica pwos.db. UNICA excepcion deliberada: POST /api/refrescar-bonos-rava
(Epica 9, Tarea 9.3) SI escribe -- ver su docstring para el porque.
Reusa valorizar_saldos_cuenta / valorizar_posiciones / obtener_precio_fx
de dashboard.py (que a su vez usa valorizacion.py) -- no reimplementa
el calculo de patrimonio en un tercer lugar.

Uso:
    uvicorn api:app --host 0.0.0.0 --port 8000

--host 0.0.0.0 escucha en todas las interfaces de red, incluida la IP
de Tailscale de esta PC -- sin esto, el celular no puede acceder aunque
Tailscale este bien configurado (ver glosario.md, "localhost").

SEGURIDAD: los datos son patrimonio financiero real. Este servidor esta
pensado para correr SOLO dentro de la red privada de Tailscale -- nunca
exponerlo a internet publico (port forwarding, servicios como ngrok,
etc.). Sin autenticacion por decision explicita (fuera de alcance de
la primera version): la proteccion hoy es enteramente perimetral
(Tailscale), no de la aplicacion.
"""
import json
import re
import subprocess
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import configuracion
import valorizacion

# ADR-033 (2026-10-27): la API valua EN VIVO durante la rueda (dolar y precios
# actuales, sin guardarlos); fuera de la rueda, o si la fuente falla, el ultimo
# cierre. Los scripts sueltos no prenden esto y siguen con el cierre.
# Modo demo (PWOS_MODO=demo): apagada, la demo se valua al cierre de su fecha de generacion.
valorizacion.VIVO_HABILITADO = not configuracion.es_demo()

from dashboard import (
    obtener_precio_fx,
    valorizar_saldos_cuenta,
    valorizar_posiciones,
    calcular_ganancia_por_activo,
    calcular_riesgo_por_ticker,
    obtener_benchmarks_macro,
    saldos_nativos_por_contenedor,
)
from renta_dividendos import calcular_renta_y_dividendos_cobrados
from movimientos_gastos import listar_movimientos_gastos
from tir_bonos import rotacion_bonos, sensibilidad_tasa_cartera
from breakeven_implicito import breakeven_inflacion, devaluacion_implicita
from tesis_bonos import crear_tesis, actualizar_tesis, eliminar_tesis, listar_tesis
from tesis_acciones import (
    crear_tesis as crear_tesis_accion, actualizar_tesis as actualizar_tesis_accion,
    eliminar_tesis as eliminar_tesis_accion, listar_tesis as listar_tesis_acciones,
)
from tesis_cartera import (
    crear_tesis as crear_tesis_cartera, actualizar_tesis as actualizar_tesis_cartera,
    eliminar_tesis as eliminar_tesis_cartera, listar_tesis as listar_tesis_cartera,
)
from tesis_inmuebles import (
    crear_tesis as crear_tesis_inmueble, actualizar_tesis as actualizar_tesis_inmueble,
    eliminar_tesis as eliminar_tesis_inmueble, listar_tesis as listar_tesis_inmuebles,
)
from propiedades import listar_propiedades, obtener_propiedad_id
from alquileres import crear_alquiler
from valuaciones import crear_valuacion, valuacion_vigente, listar_valor_inmuebles, valor_inmuebles_usd
from pasivos import crear_pago_pasivo, listar_pasivos, saldo_total_pasivos_usd
from flujo_inmueble import flujo_inmueble, PropiedadNoAplicaError, TASA_DESCUENTO_DEFAULT
from snapshot_portafolio import armar_snapshot
from tesis_umbrales import (
    crear_umbral, listar_umbrales_de_tesis, eliminar_umbral, eliminar_umbrales_de_tesis,
)
from brecha_cambiaria import brecha_ccl_mep_historico
from alertas_umbrales import chequear_umbrales
from cargar_mercado_renta_fija_historico import listar_historico as listar_historico_rf
from ratio_bonos import ratio_historico, tickers_con_historial
from cargar_benchmarks_macro import listar_macro_historico, rem_devaluacion_pct
import indicadores_macro
from calendario_flujo import calendario_flujo_mensual, flujos_otros_pasivos
import flujo_estimado
import conciliar_flujos
from simulador_bonos import simular as simular_bono
import clasificar_bonos
from rebalanceo_rv import rebalanceo_renta_variable
from correlacion_rv import analisis_riesgo_rv
from frontera_rv import (
    TOPE_DEFAULT as FRONTERA_TOPE_DEFAULT,
    VENTANA_DEFAULT_SEM as FRONTERA_VENTANA_DEFAULT,
    VENTANA_MIN_SEM as FRONTERA_VENTANA_MIN,
    analisis_frontera_rv,
)
from tenencias_rv import tenencias_rv, composicion_rv, obtener_tesis_rv
from parametros_rv import listar_parametros, actualizar_parametro, leer_parametros
import cedear_moneda
import cedear_caro_barato
from dry_run_precios import obtener_mep, obtener_ccl
import indicadores_rv
import watchlist_rv
from screener_rv import screener, valuacion_de_ticker
import simulador_rv
from valorizacion import categoria_activo, RENTA_FIJA, RENTA_VARIABLE
from rendimiento import fechas_ancla_disponibles, serie_rendimiento, serie_rendimiento_mensual, punto_provisorio
import cierres_mensuales
import precios_vivo
from riesgo import SIN_DATO as RIESGO_SIN_DATO, bucket_de_score

DB_PATH = Path(__file__).parent / "pwos.db"
STATIC_DIR = Path(__file__).parent / "static"
DATABASE_DIR = Path(__file__).parent

app = FastAPI(title="PWOS Dashboard API")


@app.middleware("http")
async def solo_lectura_en_demo(request, call_next):
    """Modo demo: todo lo que no es lectura (altas, ediciones, refrescos y los
    endpoints que corren scripts, que son todos POST) responde 403."""
    if configuracion.es_demo() and request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse(status_code=403, content={"detail": "PWOS en modo demo: solo lectura"})
    return await call_next(request)


@app.middleware("http")
async def sin_cache(request, call_next):
    """2026-09-14, encontrado tras deployar la Epica 13: el usuario hizo un
    refresh normal (F5) y siguio viendo el `app.js`/`index.html` de
    ANTES del deploy -- StaticFiles no manda ningun Cache-Control, asi
    que el navegador puede reusar su copia en cache sin siquiera
    revalidar contra el servidor (heuristica estandar para respuestas
    sin esa cabecera). `no-cache` (no `no-store`) fuerza revalidacion
    SIEMPRE -- sigue siendo barato (304 si `app.js` no cambio, via el
    ETag/Last-Modified que StaticFiles ya manda), pero un deploy nuevo
    ya no puede quedar invisible detras de un F5 comun. Aplicado a
    TODAS las respuestas (estaticos Y /api/*) a proposito: en un
    dashboard financiero, JSON viejo servido por el navegador sin
    pegarle al servidor es el mismo problema que un app.js viejo."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


# Whitelist explicita de brokers -> script de dry-run (Tarea 8.2, n8n), de la
# configuracion del usuario (vacia en la demo: el endpoint responde 400).
# Mapa explicito, no heuristica: este endpoint corre un script en el
# sistema, no hay margen para adivinar que "broker" es valido. Cada
# script autodetecta la carpeta de descargas/ mas reciente por su cuenta
# (ver incremental_utils.carpeta_descarga_mas_reciente) - el endpoint solo
# elige QUE script correr.
DRY_RUN_SCRIPTS = {
    b["clave_dry_run"]: b["dry_run"] for b in configuracion.brokers().values() if b.get("dry_run")
}
DRY_RUN_TIMEOUT_SEG = 60


def _conectar() -> sqlite3.Connection:
    if configuracion.es_demo():  # ademas del 403: la base de la demo se abre de solo lectura
        return sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True)
    return sqlite3.connect(DB_PATH)


@app.get("/api/resumen")
def resumen():
    """Patrimonio total + subtotales de efectivo/inversiones + cotizaciones
    del dia + ganancia/perdida TOTAL de la cartera (metric card nueva del
    header, rediseño 2026-08-19) -- suma de invertido/valor_actual de
    calcular_ganancia_por_activo, no un promedio de porcentajes.

    CAMBIO 2026-08-27 -- el usuario encontro que este card solo sumaba la
    ganancia NO realizada (posiciones todavia abiertas), ignorando por
    completo la ganancia REALIZADA por ventas (Parte A, 2026-08-26,
    `ganancia_realizada_usd`/`costo_vendido_usd`/`producido_usd` de
    calcular_ganancia_por_activo) -- el header mostraba "perdida" o un
    numero mucho mas chico del real porque plata ya efectivamente
    ganada en ventas pasadas no entraba a la cuenta. Ahora suma ambas:
    retorno NO realizado (valor actual + amortizacion - invertido de lo
    que sigue abierto) MAS retorno realizado (producido - costo vendido
    de lo que ya se vendio). El % tambien se corrigio para no quedar
    inflado/desinflado: el denominador es el costo TOTAL alguna vez
    puesto en juego (invertido remanente + costo ya liberado por
    ventas), no solo el de lo que sigue abierto -- si no, sumar
    ganancia realizada al numerador sin ajustar el denominador
    distorsiona el porcentaje.

    El agregado SOLO suma lo "verificable" en cada lado (no realizado:
    invertido_usd no es None; realizado: ganancia_realizada_verificable)
    -- lo no verificable (compras en especie sin tipo de cambio real,
    o ventas con el mismo problema, ver calcular_ganancia_por_activo)
    queda afuera del total en vez de arrastrar un numero sin base real
    al header."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        saldos = valorizar_saldos_cuenta(con, mep)
        posiciones = valorizar_posiciones(con, mep, ccl)
        total_efectivo = sum(f["valor_usd"] for f in saldos)
        total_inversiones = sum(
            f["valor_usd"] for f in posiciones if f["valor_usd"] is not None
        )

        # Real Estate (2026-09-18): `total_inmuebles` es SOLO propiedades
        # `tipo='activo_fisico'` con valuacion cargada (valor BRUTO, sin
        # netear deuda -- ver valuaciones.py::listar_valor_inmuebles).
        # Un fondo inmobiliario (`fondo_re`) NO entra aca: ya esta contado en
        # `total_efectivo` via su propia cuenta (ver
        # migrar_v228_valuaciones_pasivos.py). `total_pasivos` es el
        # saldo vigente de TODOS los pasivos, tengan o no propiedad
        # vinculada -- se resta siempre, no solo mientras se esta
        # pagando.
        total_inmuebles = valor_inmuebles_usd(con)
        total_pasivos = saldo_total_pasivos_usd(con)
        # Total de activos (bruto, antes de deudas): la UNICA fuente para el
        # header y el centro de la dona (2026-10-30). La raiz del arbol de
        # composicion suma hojas ya redondeadas y difiere en centavos.
        total_activos = total_efectivo + total_inversiones + total_inmuebles

        ganancia = calcular_ganancia_por_activo(con, mep, ccl)

        no_realizada_verificable = [f for f in ganancia if f["invertido_usd"] is not None]
        invertido_abierto = sum(f["invertido_usd"] for f in no_realizada_verificable)
        # retorno no realizado = valor actual + amortizacion ya cobrada
        # (fix 2026-08-21, ver dashboard.py::calcular_ganancia_por_activo)
        # -- tiene que sumar lo mismo que el panel, no solo valor_actual.
        retorno_abierto = sum(
            f["valor_actual_usd"] + f["amortizacion_cobrada_usd"] for f in no_realizada_verificable
        )

        realizada_verificable = [f for f in ganancia if f["tuvo_ventas"] and f["ganancia_realizada_verificable"]]
        costo_vendido = sum(f["costo_vendido_usd"] for f in realizada_verificable)
        producido = sum(f["producido_usd"] for f in realizada_verificable)

        total_invertido = invertido_abierto + costo_vendido
        total_retorno = retorno_abierto + producido
        ganancia_usd = total_retorno - total_invertido
        ganancia_porcentaje = (ganancia_usd / total_invertido * 100) if total_invertido else 0.0

        # Riesgo de cartera (metric card, 2026-08-22): score ponderado por
        # el peso de cada posicion en INVERSIONES (efectivo no participa,
        # mismo criterio que _arbol_por_riesgo). Solo pesa posiciones con
        # score real -- una posicion "No verificable" (ver riesgo.py) no
        # se inventa un numero, simplemente no entra en el promedio, y su
        # peso se reporta aparte en riesgo_cobertura_pct para que no
        # quede escondido.
        riesgo_por_ticker = calcular_riesgo_por_ticker(con)
        valor_con_score = 0.0
        suma_ponderada = 0.0
        for f in posiciones:
            if f["valor_usd"] is None:
                continue
            info = riesgo_por_ticker.get(f["ticker"])
            if info is None or info["score_final"] is None:
                continue
            valor_con_score += f["valor_usd"]
            suma_ponderada += f["valor_usd"] * info["score_final"]

        riesgo_score = (suma_ponderada / valor_con_score) if valor_con_score else None
        riesgo_bucket = bucket_de_score(riesgo_score) if riesgo_score is not None else RIESGO_SIN_DATO
        riesgo_cobertura_pct = (valor_con_score / total_inversiones * 100) if total_inversiones else 0.0

        # Sensibilidad de tasa (BPV/Carry/Breakeven, metric cards
        # 2026-09-11): BPV es magnitud positiva (ver
        # tir_bonos.py::sensibilidad_tasa_cartera), el front lo muestra
        # en negativo ("-$X por +1pb de suba de tasas").
        sensibilidad = sensibilidad_tasa_cartera(con, mep, ccl)

        return {
            "mep": mep,
            "ccl": ccl,
            "total_efectivo": round(total_efectivo, 2),
            "total_inversiones": round(total_inversiones, 2),
            "total_inmuebles": round(total_inmuebles, 2),
            "total_pasivos": round(total_pasivos, 2),
            "total_activos_usd": round(total_activos, 2),
            "patrimonio_total": round(total_activos - total_pasivos, 2),
            "ganancia_usd": round(ganancia_usd, 2),
            "ganancia_porcentaje": round(ganancia_porcentaje, 2),
            "riesgo_score": round(riesgo_score, 2) if riesgo_score is not None else None,
            "riesgo_bucket": riesgo_bucket,
            "riesgo_cobertura_pct": round(riesgo_cobertura_pct, 1),
            "bpv_total_usd": sensibilidad["bpv_total_usd"],
            "carry_pct_total": sensibilidad["carry_pct_total"],
            "breakeven_total_pb": sensibilidad["breakeven_total_pb"],
            "benchmarks_macro": obtener_benchmarks_macro(con),
            # De cuando es el dato con el que se valuo (ADR-033): "vivo" con su
            # hora, o "cierre" con su fecha, y los activos que cayeron al cierre.
            "cotizaciones": valorizacion.cotizaciones_para_pantalla(con),
        }
    finally:
        con.close()


@app.get("/api/snapshot-portafolio")
def snapshot_portafolio():
    """Snapshot de solo lectura de la cartera completa (liquidez / mercado
    financiero / real estate), patrimonio BRUTO sin pasivos -- ver
    snapshot_portafolio.py para la definicion de `exposicion` (moneda
    economica y riesgo de custodia, ejes independientes)."""
    con = _conectar()
    try:
        return armar_snapshot(con)
    finally:
        con.close()


@app.get("/api/ganancia-por-activo")
def ganancia_por_activo():
    """Ganancia/pérdida no realizada por instrumento (tabla del panel
    central del rediseño 2026-08-19) -- ver docstring de
    calcular_ganancia_por_activo (dashboard.py) para el método completo."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        return calcular_ganancia_por_activo(con, mep, ccl)
    finally:
        con.close()


@app.get("/api/renta-dividendos-cobrados")
def renta_dividendos_cobrados():
    """Renta de bonos y dividendos de acciones/CEDEARs YA COBRADOS en el
    año en curso (complemento de lo PROYECTADO del panel de Flujo de
    pagos) -- ver renta_dividendos.py para el método completo (exclusión
    de interés de caución y de amortización, bucket "sin_atribuir" para
    los dividendos de un broker cuyo export no trae el ticker)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        return calcular_renta_y_dividendos_cobrados(con, mep, ccl)
    finally:
        con.close()


@app.get("/api/movimientos-gastos")
def movimientos_gastos(anio: int | None = None):
    """Movimientos de la esfera consumo (ingreso/egreso) -- primer
    modulo nuevo de la app (Epica 10, "Movimientos y gastos"). Filtro
    vía esferas.tipos_de_esfera("consumo"), no una lista hardcodeada
    aca -- ver movimientos_gastos.py. `categoria` viene en null en
    todas las filas hasta que se siembre un arbol real en `categorias`
    (vacia hoy, auditada en la Épica 10)."""
    con = _conectar()
    try:
        return listar_movimientos_gastos(con, anio=anio)
    finally:
        con.close()


@app.get("/api/rotacion-bonos")
def rotacion_bonos_endpoint():
    """Tabla "Bonos en tenencia" de la solapa Analisis - Bonos: por bono
    de renta fija vigente, PPC / precio hoy / paridad / invertido /
    valorizacion + TIR de entrada vs. TIR de hoy (compresion). Ver
    tir_bonos.py. `invertido_usd` se toma de calcular_ganancia_por_activo
    (mismo criterio que el panel de Ganancia: cada compra al MEP/CCL de
    SU fecha) -- reconcilia PPC/invertido/valorizacion en una sola vista.
    `candidatos_reemplazo` sigue en la respuesta (lo usa el reporte CLI)
    pero el dashboard ya no lo muestra."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        filas = rotacion_bonos(con, mep, ccl)
        invertido_por_ticker = {
            f["ticker"]: f["invertido_usd"]
            for f in calcular_ganancia_por_activo(con, mep, ccl)
        }
        for fila in filas:
            # Un bono que entro por canje (monto 0) sale de Ganancia con invertido None:
            # queda el invertido representativo que ya puso rotacion_bonos.
            invertido = invertido_por_ticker.get(fila["ticker"])
            fila["invertido_usd"] = invertido if invertido is not None else fila.get("invertido_usd")
        return filas
    finally:
        con.close()


@app.get("/api/calendario-flujo")
def calendario_flujo_endpoint():
    """Epica 11, Herramienta 2 (paso 1): flujo de los proximos 12 meses,
    renta fija PROYECTADA (tenencia vigente x cronograma, reusa
    vista_flujo_fondos_bonos) + ingreso pasivo REAL ya cobrado en bancos/
    billeteras virtuales (interes de plazo fijo, alquiler -- 2026-09-17,
    pedido del usuario). `detalle` por mes trae la fecha exacta de cada
    movimiento. Meses "flojos" marcados (por debajo del 50% del promedio
    de la ventana -- ver calendario_flujo.py)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        return calendario_flujo_mensual(con, mep, meses=12)
    finally:
        con.close()


@app.get("/api/flujo-cobros")
def flujo_cobros_endpoint():
    """Panel "Flujo de pagos mensual" del Dashboard (ticket "estimado vs.
    real", 2026-10-05, schema v2.45): cada cobro (renta y amortizacion de
    bonos, alquileres, interes bancario) con fecha exacta, contenedor donde
    entra, monto en su moneda y en USD, y estado
    estimado/a_confirmar/cobrado/no_cobrado; + agregado por mes y el
    objetivo de renta. Antes de leer sincroniza los estimados guardados en
    `flujos_esperados` (flujo_estimado.py: local, sin red, solo escribe si
    cambio la tenencia o un contrato) y los concilia con los movimientos
    reales ya cargados (conciliar_flujos.py, Tanda 3). Si la sincronizacion falla (base
    ocupada por otra escritura) se muestra lo ya guardado, con el aviso."""
    con = _conectar()
    try:
        aviso, ambiguos = None, {}
        # La demo ya viene sincronizada y su base es de solo lectura.
        if not configuracion.es_demo():
            try:
                flujo_estimado.sincronizar(con)
                # Tanda 3: vincula lo que la ronda incremental ya cargo (solo los
                # casos sin empate; los empates vuelven como `candidatos`).
                ambiguos = conciliar_flujos.conciliar(con)["ambiguos"]
            except sqlite3.Error as e:
                aviso = f"No se pudieron actualizar los estimados ({e}); se muestran los ya guardados."
        resultado = flujo_estimado.flujo_cobros(con, ambiguos=ambiguos)
        riesgo_por_ticker = calcular_riesgo_por_ticker(con)
        for ev in resultado["eventos"]:
            ev["riesgo_bucket"] = (
                riesgo_por_ticker.get(ev["concepto"], {}).get("bucket", RIESGO_SIN_DATO)
                if ev["tipo"] in ("cupon", "amortizacion") else None
            )
        resultado["aviso"] = aviso
        return resultado
    finally:
        con.close()


@app.get("/api/rebalanceo-rv")
def rebalanceo_rv_endpoint():
    """Epica 11, Herramienta 3: peso actual vs. peso objetivo (tesis
    cargada a mano por el usuario) por posicion de Renta Variable. Ver
    rebalanceo_rv.py -- sin tesis, la fila trae `motivo` en vez de
    `accion`, nunca se inventa un objetivo."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        return rebalanceo_renta_variable(con, mep, ccl)
    finally:
        con.close()


@app.get("/api/bonos-mercado")
def bonos_mercado_endpoint():
    """Epica 11, analizador/simulador de bonos: snapshot cacheado de TODO
    el universo de renta fija que publica Rava (~890 especies) -- la
    tabla que el usuario usaba en docta.com.ar, sin salir del dashboard.
    Refrescado por n8n via POST /api/refrescar-mercado-bonos (Opcion B).

    tir/duration/dm/convexity los calcula Rava a partir del precio real
    de mercado -> se muestran tal cual (matchean docta por
    construccion). `paridad` y `current_yield` NO se incluyen: el
    endpoint masivo de Rava los devuelve en 0 para la mayoria de los
    bonos (verificado 2026-09-08) -- se calculan bien por ticker en
    /api/simular-bono. `precio` es el CRUDO de Rava (sin normalizar
    moneda ni escala) -> va marcado como tal en el frontend, NO se usa
    para calcular nada.

    `categoria` (2026-09-08): clasificacion por
    categoria de mercado (Letras / BONCAP / Bonares / Globales / BOPREAL
    / CER / dollar-linked / provinciales / ON / otros) via
    clasificar_bonos.py -- por patron de ticker, porque Rava no da
    nombre ni tipo utilizable para el 57% de las especies. `en_cartera`
    y `es_variante_especie` (sufijo C/D de la misma emision, ej.
    AL30C/AL30D vs AL30) para que el frontend pueda filtrar/agrupar sin
    recalcular."""
    con = _conectar()
    try:
        tickers_cartera = {
            r[0] for r in con.execute(
                """
                SELECT DISTINCT a.ticker FROM vista_posiciones vp
                JOIN activos a ON a.id = vp.activo_id
                WHERE a.tipo IN ('bono_soberano','bono_provincial','bono_cer','on','lecap');
                """
            ).fetchall()
        }
        filas = con.execute(
            """
            SELECT ticker, nombre, tipo_instrumento, legislacion, tir, duration,
                   dm, convexity, valor_tecnico, precio, vencimiento, actualizado_en,
                   emisor, descripcion, isin, moneda_especie,
                   precio_limpio_vn100, paridad_calc, current_yield_calc, enriquecido_en
            FROM mercado_renta_fija_rava
            WHERE tir IS NOT NULL
            ORDER BY ticker;
            """
        ).fetchall()
        tickers_todos = {r[0] for r in filas}
        cols = ["ticker", "nombre", "tipo_instrumento", "legislacion", "tir_pct",
                "duration", "dm", "convexity", "valor_tecnico", "precio_crudo_rava",
                "vencimiento", "actualizado_en",
                "emisor", "descripcion", "isin", "moneda_especie",
                "precio_limpio_vn100", "paridad_calc", "current_yield_calc", "enriquecido_en"]

        def _es_variante(tk: str) -> bool:
            base = clasificar_bonos._sin_sufijo_especie(tk)
            return base != tk and base in tickers_todos

        def fila_dict(r):
            d = dict(zip(cols, r))
            if d["tir_pct"] is not None:  # Rava lo da como fraccion (0.08) -> %
                d["tir_pct"] = round(d["tir_pct"] * 100, 3)
            for k in ("duration", "dm", "convexity", "valor_tecnico", "precio_limpio_vn100"):
                if d[k] is not None:
                    d[k] = round(d[k], 4)
            # paridad_calc / current_yield_calc guardados como fraccion -> %
            d["paridad_pct"] = round(d.pop("paridad_calc") * 100, 2) if d.get("paridad_calc") is not None else None
            d["current_yield_pct"] = round(d.pop("current_yield_calc") * 100, 3) if d.get("current_yield_calc") is not None else None
            if d["actualizado_en"]:
                d["actualizado_en"] = d["actualizado_en"][:10]
            if d.get("enriquecido_en"):
                d["enriquecido_en"] = d["enriquecido_en"][:10]
            cat = clasificar_bonos.clasificar(d["ticker"], d["tipo_instrumento"], d["legislacion"])
            d["categoria"] = cat
            d["categoria_label"] = clasificar_bonos.CATEGORIA_LABEL[cat]
            d["en_cartera"] = d["ticker"] in tickers_cartera
            d["es_variante_especie"] = _es_variante(d["ticker"])
            return d

        especies = [fila_dict(r) for r in filas]
        conteo = {clave: 0 for clave, _ in clasificar_bonos.CATEGORIAS}
        for e in especies:
            conteo[e["categoria"]] += 1
        categorias = [
            {"clave": clave, "label": label, "total": conteo[clave]}
            for clave, label in clasificar_bonos.CATEGORIAS
        ]
        return {
            "especies": especies,
            "total": len(especies),
            "categorias": categorias,
            "en_cartera_total": sum(1 for e in especies if e["en_cartera"]),
        }
    finally:
        con.close()


@app.get("/api/simular-bono/{ticker}")
def simular_bono_endpoint(ticker: str, precio: float | None = None,
                          tir_objetivo: float | None = None):
    """Epica 11, analizador/simulador de bonos. Devuelve:
      - `mercado`: foto de hoy (TIR/duration/dm/convexidad de Rava +
        paridad/current yield calculados sobre el precio LIMPIO que
        implica esa TIR -- nunca el `precio` crudo del endpoint masivo).
      - `posicion`: tu tenencia + tu precio PWOS, si el bono esta en
        cartera.
      - `cronograma`: pagos futuros (interes/amortizacion) por VN 100.
      - `simulacion`: si se pasa `precio` (por VN 100, en la moneda de
        pago del bono) o `tir_objetivo` (%), las metricas a ese punto +
        el delta de precio vs. mercado.

    Cronograma de bonos EN cartera: de cronograma_pagos_bonos (Rava,
    cacheado). Cualquier otra especie: Rava en vivo
    (rava_client.obtener_flujo_bono). Ver simulador_bonos.py."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        res = simular_bono(con, ticker, precio_vn100=precio,
                           tir_objetivo_pct=tir_objetivo, mep=mep)
        if "error" in res:
            raise HTTPException(status_code=404, detail=res["error"])
        return res
    finally:
        con.close()


@app.get("/api/bonos-cartera-por-categoria")
def bonos_cartera_por_categoria():
    """% de tenencia de renta fija por categoria de mercado (Letras /
    BONCAP / TAMAR / Duales / CER / Bonares / Globales / BOPREAL /
    Provinciales / ON / ...) -- panel de barras de la solapa Analisis -
    Bonos (2026-09-09). Valor por posicion = el mismo
    `valor_actual_usd` de calcular_ganancia_por_activo que ya alimenta la
    torta del dashboard; categoria via clasificar_bonos (por patron de
    ticker + tipo_instrumento/legislacion de mercado_renta_fija_rava)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        meta = {
            tk: (ti, le) for tk, ti, le in con.execute(
                "SELECT ticker, tipo_instrumento, legislacion FROM mercado_renta_fija_rava;"
            )
        }
        rf = {"bono_soberano", "bono_provincial", "bono_cer", "on", "lecap"}
        por_cat: dict[str, float] = {}
        for f in calcular_ganancia_por_activo(con, mep, ccl):
            if f["tipo"] not in rf or not f.get("posicion_abierta"):
                continue
            valor = f.get("valor_actual_usd") or 0.0
            if valor <= 0:
                continue
            ti, le = meta.get(f["ticker"], (None, None))
            cat = clasificar_bonos.clasificar(f["ticker"], ti, le)
            por_cat[cat] = por_cat.get(cat, 0.0) + valor

        total = sum(por_cat.values())
        filas = [
            {
                "categoria": clave,
                "categoria_label": clasificar_bonos.CATEGORIA_LABEL[clave],
                "valor_usd": round(v, 2),
                "pct": round(v / total * 100, 2) if total else 0.0,
            }
            for clave, v in por_cat.items()
        ]
        filas.sort(key=lambda x: x["valor_usd"], reverse=True)
        return {"total_usd": round(total, 2), "categorias": filas}
    finally:
        con.close()


@app.get("/api/bonos-sensibilidad-por-categoria")
def bonos_sensibilidad_por_categoria():
    """BPV (DV01) y Carry (running yield) de la cartera de renta fija
    por categoria de mercado -- panel al lado de "Tenencias de bonos
    por categoria" (2026-09-11). A diferencia de aquel
    (concentracion por VALOR), este mide concentracion de RIESGO DE
    TASA y cuanto breakeven de carry hay contra ese riesgo. Ver
    tir_bonos.py::sensibilidad_tasa_cartera."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        return sensibilidad_tasa_cartera(con, mep, ccl)
    finally:
        con.close()


def _rem_detalle(con) -> dict | None:
    """`detalle_json` (ya parseado) de la fila `clave='rem'` de
    `benchmarks_macro` -- mismo dato que expone /api/macro-panel, leido
    aca para armar el veredicto `conviene` de breakeven/devaluacion
    implicita (Epica 14 Parte 1, 2026-09-14) sin duplicar la carga del
    REM en un segundo lugar."""
    fila = con.execute(
        "SELECT detalle_json FROM benchmarks_macro WHERE clave = 'rem';"
    ).fetchone()
    if not fila or not fila[0]:
        return None
    return json.loads(fila[0])


@app.get("/api/breakeven-inflacion")
def breakeven_inflacion_endpoint():
    """Breakeven de inflacion (2026-09-11): 1 fila por
    bono CER de TODO el universo de Rava, emparejado con el bono a tasa
    fija (LECAP/BONCAP) de duration mas cercana -- la diferencia de TIR
    (misma moneda, resta simple) es la inflacion que el mercado esta
    pricing in. Ver breakeven_implicito.py para la metodologia completa
    (por que resta y no formula multiplicativa, filtros de calidad de
    dato, por que matching por vecino mas cercano y no interpolacion de
    curva).

    Epica 14 Parte 1 (2026-09-14): cada fila suma `conviene` contra la
    inflacion prox. 12m del REM (BCRA, cacheado en `benchmarks_macro`)."""
    con = _conectar()
    try:
        detalle = _rem_detalle(con)
        inflacion_rem = detalle.get("inflacion_prox12m_pct") if detalle else None
        return breakeven_inflacion(con, inflacion_rem)
    finally:
        con.close()


@app.get("/api/devaluacion-implicita")
def devaluacion_implicita_endpoint():
    """Devaluacion implicita (2026-09-11): 1 fila por
    bono a tasa fija en pesos (LECAP/BONCAP) de TODO el universo de
    Rava, emparejado con el bono dollar-linked o hard-dollar (Bonares/
    Globales/dollar-linked) de duration mas cercana -- la devaluacion
    que el mercado esta pricing in via paridad de tasas (formula
    MULTIPLICATIVA, a diferencia del breakeven de inflacion: aca se
    cruzan 2 monedas). Ver breakeven_implicito.py para la metodologia
    completa.

    Epica 14 Parte 1 (2026-09-14): cada fila suma `conviene` contra la
    devaluacion implicita en el tipo de cambio proyectado del REM (BCRA,
    cacheado en `benchmarks_macro`)."""
    con = _conectar()
    try:
        detalle = _rem_detalle(con)
        devaluacion_rem = rem_devaluacion_pct(detalle)
        return devaluacion_implicita(con, devaluacion_rem)
    finally:
        con.close()


class TesisBonoBody(BaseModel):
    ticker: str | None = None
    fecha: str
    tesis: str
    riesgos: str | None = None
    conviccion: str | None = None


class TesisBonoUpdateBody(BaseModel):
    ticker: str | None = None
    fecha: str | None = None
    tesis: str | None = None
    riesgos: str | None = None
    conviccion: str | None = None
    estado: str | None = None
    reflexion_final: str | None = None
    fecha_reflexion: str | None = None


@app.get("/api/tesis-bonos")
def tesis_bonos_listar():
    """Bitacora de tesis de inversion de bonos (2026-09-12, punto 3 de
    la ronda de Bonos) -- lista COMPLETA, cronologica (mas reciente
    primero), sin filtros server-side: el front filtra por estado/
    instrumento en JS (mismo criterio que "Bonos en tenencia"/"Mercado
    de bonos"). Ver tesis_bonos.py."""
    con = _conectar()
    try:
        return listar_tesis(con)
    finally:
        con.close()


@app.post("/api/tesis-bonos")
def tesis_bonos_crear(body: TesisBonoBody):
    """Crea una tesis nueva (macro si `ticker` es None, micro si apunta
    a un bono) -- siempre nace en estado 'vigente'. Ver tesis_bonos.py::
    crear_tesis para las validaciones (conviccion en rango; `ticker` es
    texto libre, sin validar contra ningun catalogo -- ver docstring
    del modulo para el motivo)."""
    con = _conectar()
    try:
        nuevo_id = crear_tesis(
            con, ticker=body.ticker, fecha=body.fecha, tesis=body.tesis,
            riesgos=body.riesgos, conviccion=body.conviccion,
        )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.put("/api/tesis-bonos/{tesis_id}")
def tesis_bonos_actualizar(tesis_id: int, body: TesisBonoUpdateBody):
    """Update parcial (editar contenido, o cerrar con reflexion_final +
    fecha_reflexion en la misma llamada) -- ver tesis_bonos.py::
    actualizar_tesis. Los campos no enviados (None) no se tocan."""
    con = _conectar()
    try:
        actualizar_tesis(con, tesis_id, **body.model_dump())
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.delete("/api/tesis-bonos/{tesis_id}")
def tesis_bonos_eliminar(tesis_id: int):
    con = _conectar()
    try:
        eliminar_umbrales_de_tesis(con, "bonos", tesis_id)
        eliminar_tesis(con, tesis_id)
        return {"ok": True}
    finally:
        con.close()


class TesisAccionBody(BaseModel):
    ticker: str | None = None
    fecha: str
    tesis: str
    riesgos: str | None = None
    conviccion: str | None = None


class TesisAccionUpdateBody(BaseModel):
    ticker: str | None = None
    fecha: str | None = None
    tesis: str | None = None
    riesgos: str | None = None
    conviccion: str | None = None
    estado: str | None = None
    reflexion_final: str | None = None
    fecha_reflexion: str | None = None


@app.get("/api/tesis-acciones")
def tesis_acciones_listar():
    """Bitacora de tesis de inversion de Renta Variable (2026-09-12,
    mismo patron que /api/tesis-bonos, ver tesis_acciones.py) -- lista
    COMPLETA, cronologica, sin filtros server-side."""
    con = _conectar()
    try:
        return listar_tesis_acciones(con)
    finally:
        con.close()


@app.post("/api/tesis-acciones")
def tesis_acciones_crear(body: TesisAccionBody):
    con = _conectar()
    try:
        nuevo_id = crear_tesis_accion(
            con, ticker=body.ticker, fecha=body.fecha, tesis=body.tesis,
            riesgos=body.riesgos, conviccion=body.conviccion,
        )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.put("/api/tesis-acciones/{tesis_id}")
def tesis_acciones_actualizar(tesis_id: int, body: TesisAccionUpdateBody):
    con = _conectar()
    try:
        actualizar_tesis_accion(con, tesis_id, **body.model_dump())
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.delete("/api/tesis-acciones/{tesis_id}")
def tesis_acciones_eliminar(tesis_id: int):
    con = _conectar()
    try:
        eliminar_umbrales_de_tesis(con, "acciones", tesis_id)
        eliminar_tesis_accion(con, tesis_id)
        return {"ok": True}
    finally:
        con.close()


class TesisCarteraBody(BaseModel):
    fecha: str
    tesis: str
    riesgos: str | None = None
    conviccion: str | None = None


class TesisCarteraUpdateBody(BaseModel):
    fecha: str | None = None
    tesis: str | None = None
    riesgos: str | None = None
    conviccion: str | None = None
    estado: str | None = None
    reflexion_final: str | None = None
    fecha_reflexion: str | None = None


@app.get("/api/tesis-cartera")
def tesis_cartera_listar():
    """Bitacora de metas/tesis de la cartera COMPLETA (2026-09-12,
    solapa Dashboard) -- sin `ticker`: no hay nocion de micro aca, ver
    tesis_cartera.py. Lista COMPLETA, cronologica, sin filtros
    server-side."""
    con = _conectar()
    try:
        return listar_tesis_cartera(con)
    finally:
        con.close()


@app.post("/api/tesis-cartera")
def tesis_cartera_crear(body: TesisCarteraBody):
    con = _conectar()
    try:
        nuevo_id = crear_tesis_cartera(
            con, fecha=body.fecha, tesis=body.tesis,
            riesgos=body.riesgos, conviccion=body.conviccion,
        )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.put("/api/tesis-cartera/{tesis_id}")
def tesis_cartera_actualizar(tesis_id: int, body: TesisCarteraUpdateBody):
    con = _conectar()
    try:
        actualizar_tesis_cartera(con, tesis_id, **body.model_dump())
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.delete("/api/tesis-cartera/{tesis_id}")
def tesis_cartera_eliminar(tesis_id: int):
    con = _conectar()
    try:
        eliminar_umbrales_de_tesis(con, "cartera", tesis_id)
        eliminar_tesis_cartera(con, tesis_id)
        return {"ok": True}
    finally:
        con.close()


class TesisInmuebleBody(BaseModel):
    ticker: str | None = None
    fecha: str
    tesis: str
    riesgos: str | None = None
    conviccion: str | None = None
    contenedor_cobro: str | None = None


class TesisInmuebleUpdateBody(BaseModel):
    ticker: str | None = None
    fecha: str | None = None
    tesis: str | None = None
    riesgos: str | None = None
    conviccion: str | None = None
    estado: str | None = None
    reflexion_final: str | None = None
    fecha_reflexion: str | None = None
    contenedor_cobro: str | None = None


@app.get("/api/tesis-inmuebles")
def tesis_inmuebles_listar():
    """Bitacora de tesis de inversion de Real Estate (Epica 15 Parte B,
    2026-09-15, mismo patron que /api/tesis-bonos -- `ticker` reusado
    como identificador corto de la propiedad, ver tesis_inmuebles.py)
    -- lista COMPLETA, cronologica, sin filtros server-side. Sin pestana
    propia todavia: vive en el Dashboard, al lado de tesis-cartera."""
    con = _conectar()
    try:
        return listar_tesis_inmuebles(con)
    finally:
        con.close()


@app.post("/api/tesis-inmuebles")
def tesis_inmuebles_crear(body: TesisInmuebleBody):
    con = _conectar()
    try:
        nuevo_id = crear_tesis_inmueble(
            con, ticker=body.ticker, fecha=body.fecha, tesis=body.tesis,
            riesgos=body.riesgos, conviccion=body.conviccion,
            contenedor_cobro=body.contenedor_cobro,
        )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.put("/api/tesis-inmuebles/{tesis_id}")
def tesis_inmuebles_actualizar(tesis_id: int, body: TesisInmuebleUpdateBody):
    con = _conectar()
    try:
        actualizar_tesis_inmueble(con, tesis_id, **body.model_dump())
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.delete("/api/tesis-inmuebles/{tesis_id}")
def tesis_inmuebles_eliminar(tesis_id: int):
    con = _conectar()
    try:
        eliminar_umbrales_de_tesis(con, "inmuebles", tesis_id)
        eliminar_tesis_inmueble(con, tesis_id)
        return {"ok": True}
    finally:
        con.close()


# ---- Real Estate -- valuacion/deuda/TIR (2026-09-18) ----
# Primeros endpoints de `propiedades.py`/`alquileres.py` (CRUD desde la
# Epica 17 Fase 4, 2026-09-17, pero sin UI/API todavia) + los nuevos de
# esta sesion (valuaciones.py/pasivos.py/flujo_inmueble.py).

class ValuacionBody(BaseModel):
    propiedad_id: int
    fecha: str
    valor_usd: float
    fuente: str | None = None


class AlquilerBody(BaseModel):
    propiedad_id: int
    periodo: str
    monto: float
    moneda: str
    fecha_cobro: str


class PagoPasivoBody(BaseModel):
    pasivo_id: int
    fecha: str
    monto_usd: float


@app.get("/api/propiedades")
def propiedades_listar():
    """Catalogo de propiedades con su valuacion vigente (bruta y la
    parte propia), el pasivo vinculado (si existe) y TIR/VPN (solo
    `activo_fisico`, tasa default 6% -- ver TASA_DESCUENTO_DEFAULT en
    flujo_inmueble.py) -- tabla del panel Real Estate. `valuacion` es
    None si la propiedad todavia no tiene ninguna fila en `valuaciones`
    (no se asume un valor).

    TIR/VPN se calculan ACA (llamado directo a flujo_inmueble(), no un
    fetch HTTP a /api/flujo-inmueble) para que el front pueda mostrarlos
    en la tabla con el MISMO request que ya trae las propiedades --
    2026-09-19: evitar N requests (uno por
    propiedad) para mostrar este dato. `tasa_descuento_pct`/
    `tasa_descuento_fuente` viajan tambien (2026-09-19, fix de la misma
    fecha: mostrar de donde sale la tasa, no solo el numero de TIR/VPN)
    -- mismos campos que devuelve /api/flujo-inmueble/{alias}, no se
    duplica el texto de la fuente en 2 lugares. Para un `fondo_re`
    (un fondo inmobiliario) todo esto queda en None directo, sin llamar a
    flujo_inmueble (que igual tirara PropiedadNoAplicaError para ese
    tipo)."""
    con = _conectar()
    try:
        pasivos_por_propiedad = {
            p["propiedad_id"]: p for p in listar_pasivos(con) if p["propiedad_id"] is not None
        }
        resultado = []
        for p in listar_propiedades(con):
            tir_pct = vpn_usd = tasa_descuento_pct = tasa_descuento_fuente = None
            if p["tipo"] == "activo_fisico":
                flujo = flujo_inmueble(con, p["id"])
                tir_pct, vpn_usd = flujo["tir_pct"], flujo["vpn_usd"]
                tasa_descuento_pct = flujo["tasa_descuento_pct"]
                tasa_descuento_fuente = flujo["tasa_descuento_fuente"]
            resultado.append({
                **p,
                "valuacion": valuacion_vigente(con, p["id"]),
                "pasivo": pasivos_por_propiedad.get(p["id"]),
                "tir_pct": tir_pct,
                "vpn_usd": vpn_usd,
                "tasa_descuento_pct": tasa_descuento_pct,
                "tasa_descuento_fuente": tasa_descuento_fuente,
            })
        return resultado
    finally:
        con.close()


@app.post("/api/valuaciones")
def valuaciones_crear(body: ValuacionBody):
    con = _conectar()
    try:
        try:
            nuevo_id = crear_valuacion(
                con, propiedad_id=body.propiedad_id, fecha=body.fecha,
                valor_usd=body.valor_usd, fuente=body.fuente,
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Ya existe una valuacion cargada para esa propiedad en {body.fecha} ({e}).",
            )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.post("/api/alquileres")
def alquileres_crear(body: AlquilerBody):
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        try:
            nuevo_id = crear_alquiler(
                con, propiedad_id=body.propiedad_id, periodo=body.periodo, monto=body.monto,
                moneda=body.moneda, mep=mep, fecha_cobro=body.fecha_cobro,
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(
                status_code=400,
                detail=f"Ya existe un alquiler cargado para esa propiedad en {body.periodo} ({e}).",
            )
        return {"ok": True, "id": nuevo_id}
    finally:
        con.close()


@app.get("/api/pasivos")
def pasivos_listar():
    """Pasivos con su saldo vigente (monto_original_usd menos la suma
    de sus pagos_pasivo, calculado al leer -- ver pasivos.py)."""
    con = _conectar()
    try:
        return listar_pasivos(con)
    finally:
        con.close()


@app.post("/api/pagos-pasivo")
def pagos_pasivo_crear(body: PagoPasivoBody):
    con = _conectar()
    try:
        nuevo_id = crear_pago_pasivo(con, pasivo_id=body.pasivo_id, fecha=body.fecha, monto_usd=body.monto_usd)
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.get("/api/flujo-inmueble/{alias}")
def flujo_inmueble_endpoint(alias: str, tasa: float = TASA_DESCUENTO_DEFAULT):
    """Flujo de caja (alquileres + pagos de pasivo + valuacion terminal
    NETA de la deuda pendiente) + TIR/VPN de una propiedad puntual --
    ver flujo_inmueble.py para el metodo completo. `alias` (ej.
    "DEPTO-EJEMPLO"), no el id numerico interno -- mismo identificador
    que usa el usuario en todo el resto de Real Estate (alta_propiedad.py,
    cargar_alquiler.py, etc.), nunca el id de `propiedades`. Solo
    aplica a propiedades `tipo='activo_fisico'` -- 400 para un
    `fondo_re`, un fondo inmobiliario (ver PropiedadNoAplicaError). Se
    recalcula siempre al vuelo, nada queda guardado -- cada alquiler/
    pago/valuacion nueva lo actualiza solo. `tasa` (anual, ej. 0.06 =
    6%) es query param, ajustable desde el front -- default importado
    de flujo_inmueble.TASA_DESCUENTO_DEFAULT (nunca hardcodeado aca, ver
    fix 2026-09-19: antes este endpoint tenia su PROPIO default de
    0.10 sin relacion con el del modulo, un 2do lugar donde ese numero
    podia quedar desactualizado)."""
    con = _conectar()
    try:
        propiedad_id = obtener_propiedad_id(con, alias)
        if propiedad_id is None:
            raise HTTPException(status_code=404, detail=f"No existe la propiedad '{alias}'.")
        try:
            return flujo_inmueble(con, propiedad_id, tasa_descuento=tasa)
        except PropiedadNoAplicaError as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


class TesisUmbralBody(BaseModel):
    tesis_tabla: str
    tesis_id: int
    indicador: str
    operador: str
    valor: float
    accion: str


@app.get("/api/tesis-umbrales")
def tesis_umbrales_listar(tesis_tabla: str, tesis_id: int):
    """Epica 12 Parte 1 (2026-09-12): umbrales de monitoreo de UNA
    tesis puntual (query params obligatorios, a diferencia de las 3
    bitacoras que traen todo de una) -- el front los pide bajo demanda
    por tesis, no de a miles como el resto. Ver tesis_umbrales.py."""
    con = _conectar()
    try:
        return listar_umbrales_de_tesis(con, tesis_tabla, tesis_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.post("/api/tesis-umbrales")
def tesis_umbrales_crear(body: TesisUmbralBody):
    con = _conectar()
    try:
        nuevo_id = crear_umbral(
            con, tesis_tabla=body.tesis_tabla, tesis_id=body.tesis_id,
            indicador=body.indicador, operador=body.operador,
            valor=body.valor, accion=body.accion,
        )
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.delete("/api/tesis-umbrales/{umbral_id}")
def tesis_umbrales_eliminar(umbral_id: int):
    con = _conectar()
    try:
        eliminar_umbral(con, umbral_id)
        return {"ok": True}
    finally:
        con.close()


@app.get("/api/umbrales-cruzados")
def umbrales_cruzados_endpoint(tesis_tabla: str | None = None, tesis_id: int | None = None):
    """Epica 12 Parte 2 (2026-09-13): evalua CADA umbral de
    `tesis_umbrales` contra el valor mas reciente de su indicador (ver
    alertas_umbrales.py) -- se recalcula en cada carga del dashboard,
    no hay nada cacheado ni programado por n8n para esto. Devuelve la
    evaluacion completa (no solo los cruzados), el front decide que
    mostrar. `tesis_tabla` opcional (cada bitacora pide solo lo suyo
    para el badge de todas sus tarjetas) + `tesis_id` opcional (una
    tarjeta puntual pidiendo el detalle de sus propios umbrales)."""
    con = _conectar()
    try:
        return chequear_umbrales(con, tesis_tabla=tesis_tabla, tesis_id=tesis_id)
    finally:
        con.close()


@app.get("/api/macro-panel")
def macro_panel():
    """Panel de contexto macro para las solapas de Analisis (2026-09-09):
    inflacion AR (INDEC) + REM (BCRA), CPI EEUU (BLS), tasa y expectativa
    de la Fed (NY Fed + cme-fedwatch), riesgo pais Argentina (EMBI+,
    2026-09-11). Todo cacheado en `benchmarks_macro`
    (1 fila por clave, UPSERT) -- se refresca con cargar_benchmarks_macro.py,
    nunca se pega a las fuentes externas en cada request. `detalle` es el
    JSON parseado de detalle_json (horizontes del REM, distribucion de
    probabilidad de la Fed)."""
    import json as _json
    con = _conectar()
    try:
        out = {}
        for clave, valor_pct, fecha_dato, fuente, detalle_json in con.execute(
            "SELECT clave, valor_pct, fecha_dato, fuente, detalle_json FROM benchmarks_macro;"
        ):
            out[clave] = {
                "valor_pct": valor_pct,
                "fecha_dato": fecha_dato,
                "fuente": fuente,
                "detalle": _json.loads(detalle_json) if detalle_json else None,
            }
        return out
    finally:
        con.close()


@app.get("/api/riesgo-rv")
def riesgo_rv_endpoint():
    """Epica 19, Fase 4 (2026-09-22): correlacion (mapa de calor + pares
    redundantes), volatilidad anualizada, contribucion al riesgo por
    posicion y diversificacion efectiva -- ver correlacion_rv.py /
    retornos_rv.py. Reemplaza /api/correlacion-rv (Epica 11): retornos
    SEMANALES (antes diarios) y subyacente USD para CEDEARs (antes todo
    via MEP). Solo informativo, no decide nada."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        t = tenencias_rv(con, r["mep"], r["ccl"], bruto)
        pesos = {p["ticker"]: p["pct_rv"] / 100 for p in t["posiciones"]}
        return analisis_riesgo_rv(con, pesos)
    finally:
        con.close()


@app.get("/api/frontera-rv")
def frontera_rv_endpoint(
    tope_pct: float = Query(FRONTERA_TOPE_DEFAULT * 100, ge=5, le=100),
    ventana_sem: int = Query(FRONTERA_VENTANA_DEFAULT, ge=FRONTERA_VENTANA_MIN, le=520),
    desvio_pp: float = Query(5.0, ge=0.5, le=50),
):
    """Epica 19, Fase 5 (2026-09-23): frontera eficiente de la RV en cartera
    -- minima varianza (guia principal), maximo Sharpe (referencia, sensible
    a retornos historicos) y la cartera actual, con tabla de desvios por
    activo. Calculo AL VUELO (patron de flujo_inmueble.py): no guarda nada,
    los insumos salen siempre de las posiciones actuales + `precios`. Ver
    frontera_rv.py. Solo informativo, nunca recomienda comprar/vender."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        t = tenencias_rv(con, r["mep"], r["ccl"], bruto)
        pesos = {p["ticker"]: p["pct_rv"] / 100 for p in t["posiciones"]}
        try:
            return analisis_frontera_rv(con, pesos, tope=tope_pct / 100, ventana_sem=ventana_sem, desvio_pp=desvio_pp)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    finally:
        con.close()


class TesisInversionBody(BaseModel):
    motivo: str | None = None
    riesgo: str | None = None
    horizonte: str | None = None
    precio_objetivo: float | None = None
    criterio_venta: str | None = None
    peso_objetivo_pct: float | None = None
    # v2.31 (Ficha de posicion / cubetas)
    cubeta: Literal["valor_puro", "calidad", "mayor_riesgo"] | None = None
    valor_intrinseco_min: float | None = None
    valor_intrinseco_max: float | None = None
    margen_seguridad_req_pct: float | None = None
    proxima_revision: date | None = None
    tendencia_secular: str | None = None
    ventaja_competitiva: str | None = None
    pre_mortem: str | None = None
    alertas_sumar: str | None = None
    alertas_reducir: str | None = None


@app.post("/api/tesis/{ticker}")
def guardar_tesis(ticker: str, body: TesisInversionBody):
    """Tesis de inversion de un ticker (Epica 11, H3; UPDATE parcial
    desde v2.31). El usuario la completa a mano, no hay feed externo posible.

    UPDATE PARCIAL: solo se tocan los campos que VIAJAN en el body. Un
    campo ausente no se toca; uno que viaja explicitamente en `null` SI
    se borra (asi el formulario de rebalanceo, que manda todos sus campos
    con null en los vacios, se comporta igual que antes). Esto es lo que
    permite guardar la Ficha de posicion por secciones (Tesis / Valuacion
    / Alertas) sobre la misma fila sin pisar las demas. Si la fila no
    existe se crea. Los nombres de columna salen de los campos del modelo
    (whitelist), nunca del input del usuario."""
    campos = body.model_dump(exclude_unset=True)
    if campos.get("proxima_revision") is not None:
        campos["proxima_revision"] = campos["proxima_revision"].isoformat()

    # Validaciones de los campos de valuacion (v2.31). Los campos viejos
    # (peso_objetivo_pct, precio_objetivo...) no se validan aca: siempre
    # aceptaron lo que mandara el formulario de rebalanceo.
    for campo in ("valor_intrinseco_min", "valor_intrinseco_max"):
        if campos.get(campo) is not None and campos[campo] < 0:
            raise HTTPException(status_code=400, detail=f"'{campo}' no puede ser negativo")
    if campos.get("margen_seguridad_req_pct") is not None and not (0 <= campos["margen_seguridad_req_pct"] <= 100):
        raise HTTPException(status_code=400, detail="'margen_seguridad_req_pct' tiene que estar entre 0 y 100")

    con = _conectar()
    try:
        fila = con.execute("SELECT id FROM activos WHERE ticker = ?;", (ticker,)).fetchone()
        if fila is None:
            raise HTTPException(status_code=404, detail=f"Ticker {ticker!r} no existe en 'activos'")
        activo_id = fila[0]

        if "valor_intrinseco_min" in campos or "valor_intrinseco_max" in campos:
            actual = con.execute(
                "SELECT valor_intrinseco_min, valor_intrinseco_max FROM tesis_inversion WHERE activo_id = ?;",
                (activo_id,),
            ).fetchone() or (None, None)
            vmin = campos.get("valor_intrinseco_min", actual[0])
            vmax = campos.get("valor_intrinseco_max", actual[1])
            if vmin is not None and vmax is not None and vmin > vmax:
                raise HTTPException(status_code=400, detail="El valor intrínseco mínimo no puede ser mayor que el máximo")
        con.execute("INSERT OR IGNORE INTO tesis_inversion (activo_id) VALUES (?);", (activo_id,))
        if campos:
            sets = ", ".join(f"{col} = ?" for col in campos)
            con.execute(
                f"UPDATE tesis_inversion SET {sets}, actualizado_en = CURRENT_TIMESTAMP WHERE activo_id = ?;",
                (*campos.values(), activo_id),
            )
        con.commit()
        return {"ok": True, "ticker": ticker, "campos_actualizados": list(campos)}
    finally:
        con.close()


@app.get("/api/tesis/{ticker}")
def obtener_tesis(ticker: str):
    """La tesis completa de un ticker (todos los campos, None si no
    cargados) para precargar la Ficha de posicion. `existe`=False si el
    ticker todavia no tiene fila de tesis."""
    con = _conectar()
    try:
        return obtener_tesis_rv(con, ticker)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        con.close()


@app.get("/api/tenencias-rv")
def tenencias_rv_endpoint():
    """Solapa Analisis - Acciones (rediseño 2026-09-21, Fase 1): tenencias
    de Renta Variable con % de la RV, % del patrimonio BRUTO, cubeta,
    topes y KPIs. Patrimonio bruto = patrimonio_total + total_pasivos de
    /api/resumen (efectivo + inversiones + inmuebles, sin restar deuda) --
    se toma de ahi, no se recalcula. Ver tenencias_rv.py."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        return tenencias_rv(con, r["mep"], r["ccl"], bruto)
    finally:
        con.close()


@app.get("/api/composicion-rv")
def composicion_rv_endpoint():
    """Cubetas vs. banda objetivo, top 5 y concentracion por sector.
    Se arma sobre tenencias_rv (mismos numeros que /api/tenencias-rv)."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        return composicion_rv(con, tenencias_rv(con, r["mep"], r["ccl"], bruto))
    finally:
        con.close()


@app.get("/api/alertas-rv")
def alertas_rv_endpoint():
    """Alertas de la solapa Acciones (revision obligatoria a -30 %,
    indicadores en rojo / amarillo 2 lecturas seguidas, topes). Misma
    lista que alimenta el KPI "Alertas activas" -- ver alertas_rv.py."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        t = tenencias_rv(con, r["mep"], r["ccl"], bruto)
        return {
            "alertas": t["alertas"],
            "activas": t["kpis"]["alertas_activas"],
            "atenuadas": t["kpis"]["alertas_atenuadas"],
            "parametros": t["parametros"],
        }
    finally:
        con.close()


def _ejecutar_indicadores(fn):
    """404 si algo referenciado no existe, 400 si el dato no es valido."""
    con = _conectar()
    try:
        return fn(con)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


class IndicadorCrearBody(BaseModel):
    ticker: str
    nombre: str
    direccion: Literal["mayor_es_mejor", "menor_es_mejor"]
    umbral_verde: float
    umbral_rojo: float
    fuente: str | None = None
    frecuencia: str | None = None


class IndicadorEditarBody(BaseModel):
    nombre: str | None = None
    fuente: str | None = None
    frecuencia: str | None = None
    direccion: Literal["mayor_es_mejor", "menor_es_mejor"] | None = None
    umbral_verde: float | None = None
    umbral_rojo: float | None = None
    activo: bool | None = None


class LecturaIndicadorBody(BaseModel):
    fecha: date
    valor: float


@app.get("/api/tesis-indicadores")
def tesis_indicadores_listar(ticker: str):
    """Indicadores de UNA posicion (activos y archivados) con su historial
    (mas reciente primero), estado actual y si lleva 2 amarillos seguidos."""
    return _ejecutar_indicadores(lambda con: indicadores_rv.listar_indicadores(con, ticker))


@app.post("/api/tesis-indicadores")
def tesis_indicadores_crear(body: IndicadorCrearBody):
    def _crear(con):
        nuevo_id = indicadores_rv.crear_indicador(
            con, ticker=body.ticker, nombre=body.nombre, direccion=body.direccion,
            umbral_verde=body.umbral_verde, umbral_rojo=body.umbral_rojo,
            fuente=body.fuente, frecuencia=body.frecuencia,
        )
        return {"ok": True, "id": nuevo_id}
    return _ejecutar_indicadores(_crear)


@app.put("/api/tesis-indicadores/{indicador_id}")
def tesis_indicadores_editar(indicador_id: int, body: IndicadorEditarBody):
    """UPDATE parcial (solo los campos que viajan). Si cambia la direccion o
    algun umbral, se recalcula el estado de todo el historial."""
    def _editar(con):
        indicadores_rv.actualizar_indicador(con, indicador_id, body.model_dump(exclude_unset=True))
        return {"ok": True, "id": indicador_id}
    return _ejecutar_indicadores(_editar)


@app.delete("/api/tesis-indicadores/{indicador_id}")
def tesis_indicadores_eliminar(indicador_id: int):
    """Borra el indicador Y todas sus lecturas. Para archivarlo sin perder
    nada: PUT con activo=false."""
    def _eliminar(con):
        indicadores_rv.eliminar_indicador(con, indicador_id)
        return {"ok": True}
    return _ejecutar_indicadores(_eliminar)


@app.post("/api/tesis-indicadores/{indicador_id}/historial")
def tesis_indicadores_cargar_lectura(indicador_id: int, body: LecturaIndicadorBody):
    """Una lectura por indicador y fecha: cargar la misma fecha corrige la
    anterior. Devuelve el estado calculado."""
    def _cargar(con):
        estado = indicadores_rv.cargar_lectura(con, indicador_id, body.fecha, body.valor)
        return {"ok": True, "estado": estado}
    return _ejecutar_indicadores(_cargar)


@app.delete("/api/tesis-indicadores/{indicador_id}/historial/{fecha}")
def tesis_indicadores_eliminar_lectura(indicador_id: int, fecha: str):
    def _eliminar(con):
        indicadores_rv.eliminar_lectura(con, indicador_id, fecha)
        return {"ok": True}
    return _ejecutar_indicadores(_eliminar)


@app.get("/api/screener-rv")
def screener_rv_endpoint():
    """Screener Graham-Plus (Fase 3): filas de cartera + watchlist con las compuertas
    C0-C3, estado de valor y margen, mas el Top 5 por margen de seguridad. Lee la
    base (fundamentales_rv), nunca le pega a Yahoo -- ver screener_rv.py."""
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        return screener(con, tenencias_rv(con, r["mep"], r["ccl"], bruto))
    finally:
        con.close()


@app.get("/api/valuacion-rv/{ticker}")
def valuacion_rv_endpoint(ticker: str):
    """Valuacion de UN ticker para la Ficha: Graham estricto vs Graham-Plus, valor
    intrinseco y estado de valor. Solo lee la base."""
    con = _conectar()
    try:
        return valuacion_de_ticker(con, ticker)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        con.close()


def _tickers_cartera(con) -> set[str]:
    mep, ccl = obtener_precio_fx(con, "USD_MEP"), obtener_precio_fx(con, "USD_CCL")
    return {p["ticker"] for p in calcular_ganancia_por_activo(con, mep, ccl)
            if p["tipo"] in ("accion", "cedear") and p["posicion_abierta"]}


class WatchlistCrearBody(BaseModel):
    ticker: str
    nombre: str | None = None
    cubeta_sugerida: Literal["valor_puro", "calidad", "mayor_riesgo"] | None = None
    notas: str | None = None


class WatchlistEditarBody(BaseModel):
    nombre: str | None = None
    cubeta_sugerida: Literal["valor_puro", "calidad", "mayor_riesgo"] | None = None
    notas: str | None = None


def _ejecutar_watchlist(fn):
    con = _conectar()
    try:
        return fn(con)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.get("/api/watchlist-rv")
def watchlist_rv_listar():
    return _ejecutar_watchlist(watchlist_rv.listar_watchlist)


@app.post("/api/watchlist-rv")
def watchlist_rv_agregar(body: WatchlistCrearBody):
    def _agregar(con):
        t = watchlist_rv.agregar(con, body.ticker, nombre=body.nombre, cubeta_sugerida=body.cubeta_sugerida,
                                 notas=body.notas, tickers_cartera=_tickers_cartera(con))
        return {"ok": True, "ticker": t}
    return _ejecutar_watchlist(_agregar)


@app.put("/api/watchlist-rv/{ticker}")
def watchlist_rv_editar(ticker: str, body: WatchlistEditarBody):
    """UPDATE parcial: solo los campos que viajan (uno en null explicito se borra)."""
    def _editar(con):
        watchlist_rv.actualizar(con, ticker, body.model_dump(exclude_unset=True))
        return {"ok": True}
    return _ejecutar_watchlist(_editar)


@app.delete("/api/watchlist-rv/{ticker}")
def watchlist_rv_eliminar(ticker: str):
    """Saca la candidata de la watchlist y borra su fila de fundamentales (si no es
    una posicion abierta)."""
    def _eliminar(con):
        watchlist_rv.eliminar(con, ticker, _tickers_cartera(con))
        return {"ok": True}
    return _ejecutar_watchlist(_eliminar)


FUNDAMENTALES_SCRIPT = "cargar_fundamentales_rv.py"
FUNDAMENTALES_TIMEOUT_COMPLETO_SEG = 900   # ~26 tickers x ~5 s de Yahoo + pausa entre tickers
FUNDAMENTALES_TIMEOUT_UNO_SEG = 120
_TICKER_ARG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,14}$")
_fundamentales_lock = threading.Lock()


@app.post("/api/refrescar-fundamentales-rv")
def refrescar_fundamentales_rv(ticker: str | None = None):
    """Corre cargar_fundamentales_rv.py como subproceso (yfinance): sin `ticker`,
    todas las posiciones abiertas + la watchlist (con backup; unos 2 minutos); con
    `ticker`, solo ese (rapido, sin backup). Misma EXCEPCION deliberada a "solo
    lectura" que el resto de los refrescos. api.py NUNCA le pega a Yahoo en un GET: el
    unico camino es este POST, a demanda (sin cron). Una sola corrida a la vez (409).
    El script recibe `--db` explicito: siempre escribe en la base que esta sirviendo
    esta API."""
    if ticker is not None and not _TICKER_ARG_RE.match(ticker):
        raise HTTPException(status_code=400, detail=f"Ticker inválido: {ticker!r}")
    if not _fundamentales_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una actualización de fundamentales en curso")
    try:
        comando = [sys.executable, FUNDAMENTALES_SCRIPT, "--db", str(DB_PATH)]
        if ticker:
            comando += ["--ticker", ticker.upper()]
        try:
            resultado = subprocess.run(
                comando, cwd=DATABASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=FUNDAMENTALES_TIMEOUT_UNO_SEG if ticker else FUNDAMENTALES_TIMEOUT_COMPLETO_SEG,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="la actualización de fundamentales no terminó a tiempo (timeout)")
    finally:
        _fundamentales_lock.release()
    cuerpo = {"ok": resultado.returncode == 0, "returncode": resultado.returncode,
              "stdout": resultado.stdout[-4000:], "stderr": resultado.stderr[-2000:]}
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


PRECIOS_RV_SCRIPT = "cargar_precios_rv.py"
PRECIOS_RV_TIMEOUT_UNO_SEG = 60


def _correr_loader_rv(script: str, ticker: str, timeout_seg: int) -> dict:
    resultado = subprocess.run(
        [sys.executable, script, "--db", str(DB_PATH), "--ticker", ticker],
        cwd=DATABASE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seg,
    )
    return {"ok": resultado.returncode == 0, "returncode": resultado.returncode,
            "stdout": resultado.stdout[-2000:], "stderr": resultado.stderr[-1000:]}


class SimularCompraBody(BaseModel):
    ticker: str
    monto_usd: float
    fuente_fondos: str   # "aporte_nuevo" o el ticker de una posicion a vender
    cubeta: Literal["valor_puro", "calidad", "mayor_riesgo"] | None = None
    sector: str | None = None
    forzar_actualizacion: bool = False   # re-descarga aunque ya haya datos del candidato


@app.post("/api/simular-compra-rv")
def simular_compra_rv_endpoint(body: SimularCompraBody):
    """Epica 19, Fase 4 (2026-09-22): Simulador de compra -- pesos, correlacion
    y riesgo ANTES/DESPUES de una compra hipotetica (ver simulador_rv.py).
    Herramienta de diagnostico, nunca dice comprar/vender. NO persiste nada
    salvo `guardar: true` (fila en `simulaciones_compra_rv`, v2.34).

    Asegura los datos del candidato ANTES de simular (7.2 del ticket:
    "dispara la carga a demanda... si hace falta"): solo corre los 2 loaders
    (cargar_fundamentales_rv.py / cargar_precios_rv.py, `--ticker` -- ad hoc,
    no hace falta que el candidato ya este en la watchlist) si el ticker
    todavia no tiene fila, o si `forzar_actualizacion`. Mismo lock y mismo
    patron de subproceso que /api/refrescar-fundamentales-rv: api.py nunca le
    pega a Yahoo en un GET, y nunca en cada simulacion si el dato ya esta."""
    try:
        ticker = watchlist_rv.normalizar_ticker(body.ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # tenencias_rv es solo lectura local (sin red): se calcula UNA vez, antes
    # del fetch del candidato, y se reusa despues -- el fetch no cambia nada
    # de la cartera, solo trae datos SOBRE el candidato. Validar aca (monto,
    # fuente_fondos) ANTES de la red: sin esto, un pedido invalido (ej.
    # vender mas de lo que hay) igual gastaba unos segundos pegandole a
    # Yahoo antes de fallar.
    r = resumen()
    bruto = r["patrimonio_total"] + r["total_pasivos"]
    con = _conectar()
    try:
        t = tenencias_rv(con, r["mep"], r["ccl"], bruto)
    finally:
        con.close()
    try:
        simulador_rv.validar_entrada(t, body.monto_usd, body.fuente_fondos)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not _fundamentales_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Ya hay una actualización de datos en curso")
    try:
        con = _conectar()
        try:
            tiene_fund = con.execute("SELECT 1 FROM fundamentales_rv WHERE ticker = ?;", (ticker,)).fetchone() is not None
            tiene_precios = con.execute("SELECT 1 FROM precios_candidatas WHERE ticker = ? LIMIT 1;", (ticker,)).fetchone() is not None
        finally:
            con.close()
        try:
            if body.forzar_actualizacion or not tiene_fund:
                r1 = _correr_loader_rv(FUNDAMENTALES_SCRIPT, ticker, FUNDAMENTALES_TIMEOUT_UNO_SEG)
                if not r1["ok"]:
                    raise HTTPException(status_code=500, detail={"paso": "fundamentales", **r1})
            if body.forzar_actualizacion or not tiene_precios:
                r2 = _correr_loader_rv(PRECIOS_RV_SCRIPT, ticker, PRECIOS_RV_TIMEOUT_UNO_SEG)
                if not r2["ok"]:
                    raise HTTPException(status_code=500, detail={"paso": "precios", **r2})
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="la actualización de datos del candidato no terminó a tiempo (timeout)")
    finally:
        _fundamentales_lock.release()

    con = _conectar()
    try:
        try:
            resultado = simulador_rv.simular(con, t, ticker, body.monto_usd, body.fuente_fondos, body.cubeta, body.sector)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return resultado
    finally:
        con.close()


class GuardarSimulacionBody(BaseModel):
    ticker: str
    monto_usd: float
    fuente_fondos: str
    resultado: dict


@app.post("/api/simulaciones-compra-rv")
def guardar_simulacion_endpoint(body: GuardarSimulacionBody):
    """Guardado EXPLICITO (7.6 del ticket) de una simulacion YA CALCULADA --
    persiste tal cual el `resultado` que ya devolvio /api/simular-compra-rv,
    sin volver a simular ni tocar la red ni el lock. Separado a proposito de
    ese endpoint: "Guardar esta simulación" en la UI persiste EXACTO lo que
    esta en pantalla, no un recalculo que podria diferir (y que competiria
    por el lock de red con cualquier otra actualizacion en curso)."""
    con = _conectar()
    try:
        nuevo_id = simulador_rv.guardar_simulacion(con, body.ticker, body.monto_usd, body.fuente_fondos, body.resultado)
        return {"ok": True, "guardado_id": nuevo_id}
    finally:
        con.close()


def _datos_mercado_cedear_demo() -> dict:
    """Modo demo: la foto del cierre que guardo generar_demo.py (ver
    cedear_moneda.leer_cierre_demo), sin red."""
    f = cedear_moneda.leer_cierre_demo()
    ctx = {"panel": f["panel"], "fecha_dato": f["fecha_dato"], "mep": f["mep"], "ccl": f["ccl"],
           "precios_ny": f["precios_ny"], "ny_error": None}
    con = _conectar()
    try:
        ctx["umbral"] = leer_parametros(con)["umbral_cedear_solo_usd_pct"]
        ctx["ratios"] = cedear_moneda.leer_ratios(con)
    finally:
        con.close()
    return ctx


def _datos_mercado_cedear() -> dict:
    """Todo lo que hace falta para evaluar, bajado UNA sola vez por pedido:
    panel de CEDEARs + MEP (502 si fallan: sin puntas o sin MEP no hay nada que
    evaluar), umbral del caso B, y para la comparacion contra NY (Fase 2) el
    CCL, los paneles usa_stocks/usa_adrs y `ratios_cedear`. Si falla solo la
    parte de NY, la herramienta sigue andando sin ella (`ny_error`)."""
    if configuracion.es_demo():
        return _datos_mercado_cedear_demo()
    # Las 4 bajadas son independientes: en paralelo (en serie tardaban ~15 s,
    # medido 2026-09-25: panel 3,8 + MEP 2,4 + CCL 2,0 + paneles NY 7,3).
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_panel = pool.submit(cedear_moneda.obtener_panel_puntas)
        f_mep = pool.submit(obtener_mep)
        f_ccl = pool.submit(obtener_ccl)
        f_ny = pool.submit(cedear_moneda.obtener_precios_ny)
    try:
        panel, fecha_dato = f_panel.result()
        mep = f_mep.result()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"No se pudo leer data912/dolarapi: {e}")
    ctx = {"panel": panel, "fecha_dato": fecha_dato, "mep": mep, "ccl": None, "precios_ny": None, "ny_error": None}
    try:
        ctx["ccl"] = f_ccl.result()
        ctx["precios_ny"] = f_ny.result()
    except Exception as e:
        ctx["ny_error"] = f"Sin comparacion contra NY: no se pudo leer data912 (usa_stocks/usa_adrs) o el CCL: {e}"
    con = _conectar()
    try:
        ctx["umbral"] = leer_parametros(con)["umbral_cedear_solo_usd_pct"]
        ctx["ratios"] = cedear_moneda.leer_ratios(con)
    finally:
        con.close()
    return ctx


def _evaluar_cedear(ticker: str, ctx: dict) -> dict:
    return cedear_moneda.evaluar_ticker(
        ticker, ctx["panel"], ctx["mep"]["venta"], ctx["umbral"],
        ratios=ctx["ratios"] if ctx["precios_ny"] is not None else None,
        precios_ny=ctx["precios_ny"], ccl=ctx["ccl"]["venta"] if ctx["ccl"] else None,
        precio_etf=cedear_moneda.precio_etf_yfinance)


def _envolver_cedear(evaluaciones, ctx: dict) -> dict:
    mep, ccl = ctx["mep"], ctx["ccl"]
    return {
        "evaluaciones": evaluaciones,
        "mep": {"venta": mep["venta"], "fecha_actualizacion": mep["fecha_actualizacion"], "fuente": "dolarapi.com (bolsa)"},
        "ccl": ({"venta": ccl["venta"], "fecha_actualizacion": ccl["fecha_actualizacion"],
                 "fuente": "dolarapi.com (contadoconliqui)"} if ccl else None),
        "fecha_dato": ctx["fecha_dato"],
        "fuera_de_horario": cedear_moneda.fuera_de_horario(ctx["fecha_dato"]),
        "consultado_en": cedear_moneda.ahora_ar(),
        "fuente": f"data912.com/live/{cedear_moneda.PANEL_CEDEARS} (punta vendedora)",
        "fuente_ny": f"data912.com/live/{'+'.join(cedear_moneda.PANELES_NY)} (ultimo operado); ETFs: yfinance",
        "ny_error": ctx["ny_error"],
        "ratios_cargados": len(ctx["ratios"]),
        "umbral_solo_usd_pct": ctx["umbral"],
        "desvio_max_vs_mep_pct": cedear_moneda.DESVIO_MAX_VS_MEP * 100,
        "desvio_max_ratio_pct": cedear_moneda.DESVIO_MAX_RATIO * 100,
    }


def _origen_cedears_cartera_watchlist() -> dict[str, set[str]]:
    """{ticker BYMA: {"cartera", "watchlist"}} de los CEDEARs con posicion
    abierta y de la watchlist (sin las acciones locales .BA)."""
    con = _conectar()
    try:
        mep_db, ccl_db = obtener_precio_fx(con, "USD_MEP"), obtener_precio_fx(con, "USD_CCL")
        cartera = {p["ticker"] for p in calcular_ganancia_por_activo(con, mep_db, ccl_db)
                   if p["tipo"] == "cedear" and p["posicion_abierta"]}
        watchlist = {w["ticker"] for w in watchlist_rv.listar_watchlist(con) if not w["ticker"].endswith(".BA")}
    finally:
        con.close()
    origen: dict[str, set[str]] = {}
    for t, o in [(t, "cartera") for t in cartera] + [(t, "watchlist") for t in watchlist]:
        origen.setdefault(cedear_moneda.ticker_byma(t), set()).add(o)
    return origen


@app.get("/api/cedear-moneda")
def cedear_moneda_todos():
    """"¿CEDEAR en pesos o en dolares?" para todos los CEDEARs en cartera
    (posicion abierta) + la watchlist, ordenado por spread (mayor primero,
    los que no se pudieron evaluar al final). Calculo en vivo, no guarda
    nada. Ver cedear_moneda.py."""
    ctx = _datos_mercado_cedear()
    origen = _origen_cedears_cartera_watchlist()
    evaluaciones = []
    for t in sorted(origen):
        ev = _evaluar_cedear(t, ctx)
        ev["origen"] = sorted(origen[t])
        evaluaciones.append(ev)
    evaluaciones.sort(key=lambda e: (e["spread_pct"] is None or e["estado"] != "ok", -(e["spread_pct"] or 0)))
    return _envolver_cedear(evaluaciones, ctx)


@app.get("/api/cedear-moneda/{ticker}")
def cedear_moneda_uno(ticker: str):
    """Evaluacion de UN CEDEAR (ticker BYMA en pesos, ej. NFLX; tambien
    acepta B.D o un ticker de Yahoo mapeado en TICKER_YAHOO_A_BYMA)."""
    if not _TICKER_ARG_RE.match(ticker or ""):
        raise HTTPException(status_code=400, detail=f"Ticker invalido: {ticker!r}")
    ctx = _datos_mercado_cedear()
    return _envolver_cedear([_evaluar_cedear(ticker, ctx)], ctx)


@app.get("/api/cedear-caro-barato")
def cedear_caro_barato_endpoint():
    """Fase 3: CCL implicito de CADA CEDEAR con ratio contra la mediana del
    mercado (set de referencia con reglas explicitas, ver cedear_caro_barato.py),
    desvio contra KO, valor teorico y badge barato/caro. Una sola bajada de
    paneles para todo el universo. La referencia usa siempre el universo
    completo; `filas` trae todo y cada fila dice si es de cartera/watchlist.
    yfinance (ETFs sin data912) SOLO para cartera/watchlist: pedir los ~58 ETFs
    de golpe tardo 103 s con 10 fallas (2026-09-25). Calculo en vivo."""
    ctx = _datos_mercado_cedear()
    if ctx["ny_error"]:
        raise HTTPException(status_code=502, detail=ctx["ny_error"])
    origen = _origen_cedears_cartera_watchlist()
    mep, ccl = ctx["mep"]["venta"], ctx["ccl"]["venta"]
    evaluaciones = []
    for t in sorted(set(ctx["ratios"]) | set(origen)):
        ev = cedear_moneda.evaluar_ticker(
            t, ctx["panel"], mep, ctx["umbral"], ratios=ctx["ratios"], precios_ny=ctx["precios_ny"], ccl=ccl,
            precio_etf=cedear_moneda.precio_etf_yfinance if t in origen else None)
        ev["origen"] = sorted(origen.get(t, ()))
        evaluaciones.append(ev)
    con = _conectar()
    try:
        umbral_alerta = leer_parametros(con)["desvio_cedear_alerta_pct"]
    finally:
        con.close()
    fuera = cedear_moneda.fuera_de_horario(ctx["fecha_dato"])
    res = cedear_caro_barato.calcular(evaluaciones, mep, ccl, umbral_alerta, fuera)
    res["filas"].sort(key=lambda f: (f["desvio_vs_ref_pct"] is None, f["desvio_vs_ref_pct"] or 0))
    res.update(fecha_dato=ctx["fecha_dato"], consultado_en=cedear_moneda.ahora_ar(), mep=mep,
               fuente_ccl_bonos="dolarapi.com (contadoconliqui)")
    return res


class ParametroRvBody(BaseModel):
    valor: float | None


@app.get("/api/parametros-rv")
def parametros_rv_endpoint():
    con = _conectar()
    try:
        return listar_parametros(con)
    finally:
        con.close()


@app.put("/api/parametros-rv/{clave}")
def actualizar_parametro_rv(clave: str, body: ParametroRvBody):
    """Edita un parametro existente de `parametros_rv` (no crea claves).
    400 si el valor no es valido, 404 si la clave no existe."""
    con = _conectar()
    try:
        try:
            actualizar_parametro(con, clave, body.valor)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Parametro {clave!r} no existe")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "clave": clave, "valor": body.valor}
    finally:
        con.close()


@app.get("/api/rendimiento-serie")
def rendimiento_serie():
    """Rendimiento total de la cartera en el tiempo (Modified Dietz,
    2026-08-21) -- ver rendimiento.py para el método completo y sus
    límites reales (la serie arranca en la primera fecha con foto de
    cartera COMPLETA, hoy 2026-07-14 -- no es todo el historial, ver
    ESTADO_PWOS.md). `fechas_ancla_disponibles` se recalcula en cada
    request en vez de cachearse: correr `importador_precios.py` de
    nuevo agrega una fecha ancla nueva sola, sin tocar este endpoint."""
    con = _conectar()
    try:
        # Fuente: cierres mensuales manuales del usuario (cierres_mensuales,
        # cargados del sheet) -- ver rendimiento.py::serie_rendimiento_mensual
        # y ESTADO_PWOS.md 2026-09-03. La reconstruccion ledger+precios
        # (serie_rendimiento) quedo para el reporte de reconciliacion.
        tramos = serie_rendimiento_mensual(con)
        return [
            {
                "fecha_inicio": t["fecha_inicio"],
                "fecha_fin": t["fecha_fin"],
                "dias": t["dias"],
                "completo": t["completo"],
                "faltantes": t["faltantes"],
                "bmv_usd": t.get("bmv_usd"),
                "emv_usd": t.get("emv_usd"),
                "flujo_neto_usd": t.get("flujo_neto_usd"),
                "rendimiento_pct": t.get("rendimiento_pct"),
                "acumulado_pct": t.get("acumulado_pct"),
                "flujos": [
                    {"fecha": f["fecha"], "monto_usd": f["monto_usd"], "descripcion": f["descripcion"]}
                    for f in t.get("flujos", [])
                ],
            }
            for t in tramos
        ]
    finally:
        con.close()


@app.get("/api/inversiones-por-tipo")
def inversiones_por_tipo():
    """Subtotal + porcentaje de INVERSIONES por tipo de activo, para el grafico
    de composicion. Posiciones sin precio cargado se excluyen del subtotal
    (mismo criterio que dashboard.py) pero no rompen el calculo."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        posiciones = valorizar_posiciones(con, mep, ccl)

        subtotales: dict[str, float] = {}
        for f in posiciones:
            if f["valor_usd"] is None:
                continue
            subtotales[f["tipo"]] = subtotales.get(f["tipo"], 0.0) + f["valor_usd"]

        total = sum(subtotales.values())
        filas = [
            {
                "tipo": tipo,
                "valor_usd": round(valor, 2),
                "porcentaje": round(valor / total * 100, 1) if total else 0.0,
            }
            for tipo, valor in subtotales.items()
        ]
        filas.sort(key=lambda f: -f["valor_usd"])
        return filas
    finally:
        con.close()


@app.get("/api/composicion-rf-rv")
def composicion_rf_rv():
    """Subtotal + porcentaje de INVERSIONES agrupado en renta_fija /
    renta_variable / otro (ver valorizacion.categoria_activo). Es el
    indicador de mas alto nivel del dashboard sobre la cartera invertida
    (Tanda 1, 2026-08-07) -- el usuario hace seguimiento de este split como
    metrica principal de su estrategia (ver ESTADO_PWOS.md seccion 6)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        posiciones = valorizar_posiciones(con, mep, ccl)

        subtotales: dict[str, float] = {}
        for f in posiciones:
            if f["valor_usd"] is None:
                continue
            categoria = categoria_activo(f["tipo"])
            subtotales[categoria] = subtotales.get(categoria, 0.0) + f["valor_usd"]

        total = sum(subtotales.values())
        filas = [
            {
                "categoria": categoria,
                "valor_usd": round(valor, 2),
                "porcentaje": round(valor / total * 100, 1) if total else 0.0,
            }
            for categoria, valor in subtotales.items()
            if valor > 1e-6  # omitir "otro" cuando no hay ninguna posicion sin clasificar
        ]
        filas.sort(key=lambda f: -f["valor_usd"])
        return filas
    finally:
        con.close()


@app.get("/api/por-contenedor")
def por_contenedor():
    """Patrimonio total (efectivo + inversiones) agrupado por contenedor
    (ALyC/broker) -- monto USD y porcentaje del total (Tanda 1,
    2026-08-07). Dinamico contra los contenedores que tengan saldo/
    posiciones reales hoy, no una lista fija de brokers -- si en el
    futuro se carga un contenedor nuevo (o uno viejo vuelve
    a tener actividad), aparece solo, sin tocar este endpoint."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        saldos = valorizar_saldos_cuenta(con, mep)
        posiciones = valorizar_posiciones(con, mep, ccl)

        subtotales: dict[str, float] = {}
        for f in saldos:
            subtotales[f["contenedor"]] = subtotales.get(f["contenedor"], 0.0) + f["valor_usd"]
        for f in posiciones:
            if f["valor_usd"] is None:
                continue
            subtotales[f["contenedor"]] = subtotales.get(f["contenedor"], 0.0) + f["valor_usd"]

        total = sum(subtotales.values())
        filas = [
            {
                "contenedor": contenedor,
                "valor_usd": round(valor, 2),
                "porcentaje": round(valor / total * 100, 1) if total else 0.0,
            }
            for contenedor, valor in subtotales.items()
            if abs(valor) > 1e-6
        ]
        filas.sort(key=lambda f: -f["valor_usd"])
        return filas
    finally:
        con.close()


def _nodo(clave: str, valor_usd: float, children: list[dict] | None = None) -> dict:
    """Nodo generico de arbol para el drill-down del dashboard (Tarea
    rediseño 2026-08-19). `clave` es el codigo estable (ticker, tipo de
    activo, contenedor, o una de las 3 categorias macro) -- el frontend
    ya resuelve la etiqueta legible y el color a partir de esa clave
    (mismos diccionarios TIPO_LABELS/CONTENEDOR_LABELS/CATEGORIA_LABELS
    que usaban los indicadores viejos, extendidos donde hacia falta) en
    vez de mandar el nombre ya traducido desde aca -- una sola fuente de
    verdad para las etiquetas, el mismo criterio que ya usaba app.js."""
    nodo = {"clave": clave, "valor_usd": round(valor_usd, 2)}
    if children:
        children = sorted(children, key=lambda c: -c["valor_usd"])
        nodo["children"] = children
    return nodo


def _hijos_por_ticker(posiciones: list[dict], filtro) -> list[dict]:
    """Agrupa posiciones (ya valorizadas) que cumplen `filtro(fila)` por
    ticker, sumando entre contenedores (una misma especie puede estar
    repartida en mas de un broker) -- son las hojas de nivel 3."""
    subtotales: dict[str, float] = {}
    for f in posiciones:
        if f["valor_usd"] is None or not filtro(f):
            continue
        subtotales[f["ticker"]] = subtotales.get(f["ticker"], 0.0) + f["valor_usd"]
    return [_nodo(ticker, valor) for ticker, valor in subtotales.items() if abs(valor) > 1e-6]


def _arbol_por_tipo(saldos: list[dict], posiciones: list[dict], inmuebles: list[dict]) -> dict:
    """Dimension 'Tipo de activo': Efectivo / Renta fija / Renta variable
    / Inmuebles en el nivel 1 -- mismos 3 baldes que ya usaba
    /api/composicion-rf-rv (valorizacion.categoria_activo), mas Efectivo
    (que ese endpoint no cubria porque solo miraba inversiones) y, desde
    2026-09-18, Inmuebles. Nivel 2 = por broker dentro de Efectivo, por
    propiedad dentro de Inmuebles, o por subtipo de activo
    (bono_soberano/on/lecap/.../accion/cedear -- el campo `tipo` que ya
    trae vista_posiciones, sin inventar una clasificacion nueva) dentro
    de RF/RV. Nivel 3 = ticker (o nada, para Inmuebles -- 2 niveles
    alcanzan, no hay una 3ra dimension debajo de cada propiedad).

    `inmuebles` es SIEMPRE valor BRUTO (valuacion x pct_propiedad, sin
    netear el pasivo asociado) -- mismo criterio que el resto de este
    arbol (ninguna otra rama neta un pasivo contra su activo, esta
    dimension es composicion de ACTIVOS, no patrimonio neto). Por eso
    el `total` que devuelve esta funcion queda, a proposito, por
    ENCIMA del patrimonio_total de /api/resumen en el monto de
    total_pasivos -- mismo patron ya documentado para el arbol
    'Riesgo' (`_arbol_por_riesgo`), que tambien difiere del total a
    proposito, por otro motivo. `pasivos` NO se agrega como rama propia
    aca: esta dimension se renderiza como donut (ver app.js::renderDonut,
    filtra `valor_usd > 0` y calcula fracciones de arco sobre el total)
    -- un valor negativo rompe esa geometria, no es solo un tema de
    gusto visual. Se resta unicamente en el escalar de /api/resumen."""
    efectivo_por_broker: dict[str, float] = {}
    for f in saldos:
        efectivo_por_broker[f["contenedor"]] = efectivo_por_broker.get(f["contenedor"], 0.0) + f["valor_usd"]
    hijos_efectivo = [_nodo(c, v) for c, v in efectivo_por_broker.items() if abs(v) > 1e-6]
    total_efectivo = sum(n["valor_usd"] for n in hijos_efectivo)

    hijos_rf = []
    for tipo in RENTA_FIJA:
        hijos_tipo = _hijos_por_ticker(posiciones, lambda f, t=tipo: f["tipo"] == t)
        if hijos_tipo:
            hijos_rf.append(_nodo(tipo, sum(n["valor_usd"] for n in hijos_tipo), hijos_tipo))
    total_rf = sum(n["valor_usd"] for n in hijos_rf)

    hijos_rv = []
    for tipo in RENTA_VARIABLE:
        hijos_tipo = _hijos_por_ticker(posiciones, lambda f, t=tipo: f["tipo"] == t)
        if hijos_tipo:
            hijos_rv.append(_nodo(tipo, sum(n["valor_usd"] for n in hijos_tipo), hijos_tipo))
    total_rv = sum(n["valor_usd"] for n in hijos_rv)

    # "otro" (tipos no clasificados en RF ni RV, ej. fci): mismo criterio
    # que /api/composicion-rf-rv, se omite si no hay ninguna posicion asi.
    hijos_otro = _hijos_por_ticker(
        posiciones, lambda f: f["tipo"] not in RENTA_FIJA and f["tipo"] not in RENTA_VARIABLE
    )
    total_otro = sum(n["valor_usd"] for n in hijos_otro)

    hijos_inmuebles = [_nodo(f["alias"], f["valor_usd_propio"]) for f in inmuebles if abs(f["valor_usd_propio"]) > 1e-6]
    total_inmuebles = sum(n["valor_usd"] for n in hijos_inmuebles)

    raiz_children = []
    if hijos_efectivo:
        raiz_children.append(_nodo("efectivo", total_efectivo, hijos_efectivo))
    if hijos_rf:
        raiz_children.append(_nodo("renta_fija", total_rf, hijos_rf))
    if hijos_rv:
        raiz_children.append(_nodo("renta_variable", total_rv, hijos_rv))
    if hijos_otro:
        raiz_children.append(_nodo("otro", total_otro, hijos_otro))
    if hijos_inmuebles:
        raiz_children.append(_nodo("inmuebles", total_inmuebles, hijos_inmuebles))

    total = total_efectivo + total_rf + total_rv + total_otro + total_inmuebles
    return _nodo("total", total, raiz_children)


def _arbol_por_broker(saldos: list[dict], posiciones: list[dict]) -> dict:
    """Dimension 'Broker': nivel 1 = contenedor (ALyC/broker), nivel 2 =
    'efectivo' (agregado, todas las cuentas/monedas de ese broker juntas
    -- el detalle por moneda queda para /api/efectivo si algun dia se
    engancha) + un nodo por ticker que tenga ese broker. Reemplaza/
    incluye a /api/por-contenedor (Tanda 1, 2026-08-07)."""
    efectivo_por_broker: dict[str, float] = {}
    pct_por_broker: dict[str, float] = {}
    for f in saldos:
        efectivo_por_broker[f["contenedor"]] = efectivo_por_broker.get(f["contenedor"], 0.0) + f["valor_usd"]
        pct_por_broker[f["contenedor"]] = f.get("pct_propiedad", 100.0)

    posiciones_por_broker: dict[str, dict[str, float]] = {}
    for f in posiciones:
        if f["valor_usd"] is None:
            continue
        por_ticker = posiciones_por_broker.setdefault(f["contenedor"], {})
        por_ticker[f["ticker"]] = por_ticker.get(f["ticker"], 0.0) + f["valor_usd"]

    contenedores = set(efectivo_por_broker) | set(posiciones_por_broker)
    raiz_children = []
    for contenedor in contenedores:
        hijos = []
        efectivo_valor = efectivo_por_broker.get(contenedor, 0.0)
        if abs(efectivo_valor) > 1e-6:
            # Contenedor compartido (ADR-032): el valor ya es la parte propia.
            pct = pct_por_broker.get(contenedor, 100.0)
            clave = "efectivo" if pct >= 100 else f"Efectivo ({pct:g}% del saldo real)"
            hijos.append(_nodo(clave, efectivo_valor))
        for ticker, valor in posiciones_por_broker.get(contenedor, {}).items():
            if abs(valor) > 1e-6:
                hijos.append(_nodo(ticker, valor))
        total_broker = sum(n["valor_usd"] for n in hijos)
        if hijos and abs(total_broker) > 1e-6:
            raiz_children.append(_nodo(contenedor, total_broker, hijos))

    total = sum(n["valor_usd"] for n in raiz_children)
    return _nodo("total", total, raiz_children)


def _arbol_por_riesgo(posiciones: list[dict], riesgo_por_ticker: dict[str, dict]) -> dict:
    """Dimension 'Riesgo' (scorecard propio, ver database/riesgo.py y
    ESTADO_PWOS.md): nivel 1 = bucket (Conservador/Moderado/Arriesgado/
    No verificable), nivel 2 = ticker. Efectivo NO participa de esta
    dimension -- el scorecard no lo cubre (sin riesgo de mercado en
    este sentido, mismo criterio que "Ganancia/perdida"), asi que el
    total de este arbol es menor al patrimonio total a proposito, no es
    un bug de agregacion."""
    por_bucket: dict[str, dict[str, float]] = {}
    for f in posiciones:
        if f["valor_usd"] is None:
            continue
        info = riesgo_por_ticker.get(f["ticker"])
        bucket = info["bucket"] if info else RIESGO_SIN_DATO
        por_ticker = por_bucket.setdefault(bucket, {})
        por_ticker[f["ticker"]] = por_ticker.get(f["ticker"], 0.0) + f["valor_usd"]

    raiz_children = []
    for bucket, por_ticker in por_bucket.items():
        hijos = [_nodo(ticker, valor) for ticker, valor in por_ticker.items() if abs(valor) > 1e-6]
        if hijos:
            raiz_children.append(_nodo(bucket, sum(n["valor_usd"] for n in hijos), hijos))

    total = sum(n["valor_usd"] for n in raiz_children)
    return _nodo("total", total, raiz_children)


@app.get("/api/composicion-jerarquica")
def composicion_jerarquica():
    """Arbol de composicion de cartera para el drill-down del dashboard
    (Tarea rediseño 2026-08-19, + dimension 'Riesgo' 2026-08-22) -- las
    3 dimensiones que ofrece el selector del panel izquierdo, calculadas
    juntas en una sola llamada (mismos datos de origen, costo marginal
    de calcular cada dimension adicional es minimo). Reemplaza a
    /api/composicion-rf-rv, /api/inversiones-por-tipo y
    /api/por-contenedor como fuente del panel de composicion -- esos 3
    endpoints quedan sin uso en el frontend nuevo, no se borraron del
    backend (podrian servir para otra cosa, y no rompen nada estando
    quietos)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        saldos = valorizar_saldos_cuenta(con, mep)
        posiciones = valorizar_posiciones(con, mep, ccl)
        riesgo_por_ticker = calcular_riesgo_por_ticker(con)
        inmuebles = listar_valor_inmuebles(con)
        return {
            "tipo_activo": _arbol_por_tipo(saldos, posiciones, inmuebles),
            "broker": _arbol_por_broker(saldos, posiciones),
            "riesgo": _arbol_por_riesgo(posiciones, riesgo_por_ticker),
        }
    finally:
        con.close()


@app.get("/api/eventos-flujo-fondos-bonos")
def eventos_flujo_fondos_bonos():
    """Eventos individuales de pago futuro de renta fija, SIN agregar por
    mes (Tarea rediseño 2026-08-19) -- para la tabla del panel de flujo
    de pagos, que se filtra en el frontend sobre estos datos ya traidos
    (un clic en una barra del grafico no dispara una consulta nueva).
    Mismos campos que ya definimos para este panel: ticker, fecha_pago,
    tipo_pago, monto_usd, es_estimado, tipo_instrumento, legislacion, +
    riesgo_bucket (2026-08-22, activa el filtro "Riesgo" del panel).
    Reusa vista_flujo_fondos_bonos (misma fuente que el endpoint mensual
    /api/flujo-fondos-bonos, que sigue existiendo para el grafico de
    barras) - no reimplementa el calculo de conversion a USD aca.

    `otros_usd` (2026-09-17): se suman tambien los
    eventos de ingreso pasivo REAL (interes de plazo fijo, alquiler) --
    `ticker` pasa a ser el nombre del contenedor para estas filas (no
    hay ticker real), y sin `tipo_instrumento`/`legislacion`/riesgo de
    bono -- `riesgo_bucket` cae a RIESGO_SIN_DATO, mismo criterio que un
    ticker sin score todavia (no hay concepto de riesgo de bono para un
    interes bancario)."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        riesgo_por_ticker = calcular_riesgo_por_ticker(con)
        filas = con.execute("""
            SELECT ticker, fecha_pago, tipo_pago, monto_usd, es_estimado,
                   tipo_instrumento, legislacion
            FROM vista_flujo_fondos_bonos
            ORDER BY fecha_pago;
        """).fetchall()
        eventos = [
            {
                "ticker": ticker,
                "fecha_pago": fecha_pago,
                "tipo_pago": tipo_pago,
                "monto_usd": round(monto_usd, 2),
                "es_estimado": bool(es_estimado),
                "tipo_instrumento": tipo_instrumento,
                "legislacion": legislacion,
                "riesgo_bucket": riesgo_por_ticker.get(ticker, {}).get("bucket", RIESGO_SIN_DATO),
            }
            for ticker, fecha_pago, tipo_pago, monto_usd, es_estimado, tipo_instrumento, legislacion in filas
        ]
        mes_desde = configuracion.hoy().replace(day=1).strftime("%Y-%m")
        eventos.extend(
            {
                "ticker": otro["concepto"],
                "fecha_pago": otro["fecha"],
                "tipo_pago": "otros",
                "monto_usd": otro["monto_usd"],
                "es_estimado": otro["es_estimado"],
                "tipo_instrumento": None,
                "legislacion": None,
                "riesgo_bucket": RIESGO_SIN_DATO,
            }
            for otro in flujos_otros_pasivos(con, mep, mes_desde)
        )
        eventos.sort(key=lambda e: e["fecha_pago"])
        return eventos
    finally:
        con.close()


@app.get("/api/efectivo")
def efectivo():
    """Detalle de cada cuenta de efectivo valorizada en USD."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        saldos = valorizar_saldos_cuenta(con, mep)
        return [
            {"concepto": f["concepto"], "valor_usd": round(f["valor_usd"], 2)}
            for f in saldos
        ]
    finally:
        con.close()


@app.get("/api/efectivo-por-contenedor")
def efectivo_por_contenedor():
    """Saldo de efectivo en moneda NATIVA por contenedor y slot de cuenta
    (disponible/caucionado x ARS/USD + USD cable) -- para cruzar contra
    cada app de broker. No convierte a USD (para eso esta /api/efectivo).
    Un slot en null = ese contenedor no tiene esa cuenta."""
    con = _conectar()
    try:
        return saldos_nativos_por_contenedor(con)
    finally:
        con.close()


@app.get("/api/flujo-fondos-bonos")
def flujo_fondos_bonos():
    """Flujo de fondos futuro de renta fija por mes calendario, en USD
    -- interés vs. amortización por separado (Tarea nueva, 2026-08-12).
    Reusa vista_flujo_fondos_mensual (que a su vez reusa
    cronograma_pagos_bonos + bonos_referencia + vista_posiciones), no
    reimplementa el calculo aca. `tiene_estimado`: true si algun monto
    del mes es un pago en ARS convertido al MEP de HOY (tipo de cambio
    futuro desconocido) -- no se mezcla sin distincion con lo que ya es
    una cifra cierta en USD.

    `otros_usd` (2026-09-17): ingreso pasivo REAL ya
    cobrado en bancos/billeteras (interés de plazo fijo, alquiler) --
    mismo `flujos_otros_pasivos` que ya usa /api/calendario-flujo, sin
    reimplementar el filtro/exclusion en un tercer lugar."""
    con = _conectar()
    try:
        mep = obtener_precio_fx(con, "USD_MEP")
        filas = con.execute(
            "SELECT mes, tipo_pago, es_estimado, monto_usd FROM vista_flujo_fondos_mensual;"
        ).fetchall()

        por_mes: dict[str, dict] = {}
        for mes, tipo_pago, es_estimado, monto_usd in filas:
            fila = por_mes.setdefault(
                mes, {"mes": mes, "interes_usd": 0.0, "amortizacion_usd": 0.0, "otros_usd": 0.0, "tiene_estimado": False}
            )
            fila[f"{tipo_pago}_usd"] += monto_usd
            fila["tiene_estimado"] = fila["tiene_estimado"] or bool(es_estimado)

        mes_desde = configuracion.hoy().replace(day=1).strftime("%Y-%m")
        for otro in flujos_otros_pasivos(con, mep, mes_desde):
            fila = por_mes.setdefault(
                otro["fecha"][:7],
                {"mes": otro["fecha"][:7], "interes_usd": 0.0, "amortizacion_usd": 0.0, "otros_usd": 0.0, "tiene_estimado": False},
            )
            fila["otros_usd"] += otro["monto_usd"]
            fila["tiene_estimado"] = fila["tiene_estimado"] or otro["es_estimado"]

        resultado = sorted(por_mes.values(), key=lambda f: f["mes"])
        for f in resultado:
            f["interes_usd"] = round(f["interes_usd"], 2)
            f["amortizacion_usd"] = round(f["amortizacion_usd"], 2)
            f["otros_usd"] = round(f["otros_usd"], 2)
            f["total_usd"] = round(f["interes_usd"] + f["amortizacion_usd"] + f["otros_usd"], 2)
        return resultado
    finally:
        con.close()


@app.post("/api/dry-run/{broker}")
def dry_run(broker: str):
    """Dispara el dry-run de solo lectura de un broker como subproceso
    (Tarea 8.2, pensado para n8n) y devuelve su salida. NUNCA inserta en
    pwos.db: el dry-run en si ya es de solo lectura por diseno, y este
    endpoint no tiene ningun camino hacia insertar_movimientos.py /
    importar_incremental (la insercion real siempre queda para que el usuario
    la corra a mano, con confirmacion explicita)."""
    script = DRY_RUN_SCRIPTS.get(broker)
    if script is None:
        raise HTTPException(
            status_code=400,
            detail=f"broker invalido: {broker!r}. Validos: {sorted(DRY_RUN_SCRIPTS)}",
        )

    try:
        resultado = subprocess.run(
            [sys.executable, script],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=DRY_RUN_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"dry-run de {broker} no termino en {DRY_RUN_TIMEOUT_SEG}s (timeout)",
        )

    cuerpo = {
        "broker": broker,
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


RAVA_REFRESH_SCRIPT = "cargar_bonos_referencia_flujo.py"
RAVA_REFRESH_TIMEOUT_SEG = 120  # 22 tickers x 2 llamados a Rava c/u, con margen


@app.post("/api/refrescar-bonos-rava")
def refrescar_bonos_rava():
    """Corre cargar_bonos_referencia_flujo.py como subproceso (Epica 9,
    Tarea 9.3, pensado para n8n semanal). EXCEPCION DELIBERADA a la
    regla de "solo lectura" del resto de este archivo -- a diferencia
    de /api/dry-run/{broker}, este endpoint SI escribe en pwos.db (UPSERT
    real vía la logica ya verificada del script, no una insercion nueva
    aca). Justificado porque bonos_referencia/cronograma_pagos_bonos son
    datos de referencia (clasificacion, cronograma de pagos publicado),
    no el ledger de movimientos/operaciones -- no hay ambiguedad de
    deduplicacion ni juicio humano que hacer para cargarlos, mismo
    criterio de "automatizar lo que ya se pueda automatizar con
    confianza" de la Epica 8. El script ya es tolerante a fallos por
    ticker y hace su propio backup antes de escribir."""
    try:
        resultado = subprocess.run(
            [sys.executable, RAVA_REFRESH_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=RAVA_REFRESH_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco de bonos Rava no termino en {RAVA_REFRESH_TIMEOUT_SEG}s (timeout)",
        )

    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


MERCADO_BONOS_REFRESH_SCRIPT = "cargar_mercado_renta_fija_rava.py"
MERCADO_BONOS_REFRESH_TIMEOUT_SEG = 90  # 1 llamado a Rava (~890 especies) + calculo
                                        # in-process de precio_limpio/paridad (sin red extra)


@app.post("/api/refrescar-mercado-bonos")
def refrescar_mercado_bonos():
    """Corre cargar_mercado_renta_fija_rava.py como subproceso. Refresca
    el snapshot de TODO el universo de renta fija de Rava
    (mercado_renta_fija_rava, ~890 especies) -- la fuente del panel
    "Mercado de bonos" y del precio de referencia del simulador
    (Opcion B: tabla cacheada, n8n la refresca en horario de mercado).

    Misma EXCEPCION deliberada a "solo lectura" que /api/refrescar-bonos-rava:
    escribe en pwos.db, pero es un UPSERT sobre una tabla de mercado
    100% derivada de Rava, re-ejecutable, sin FK, sin juicio humano. El
    script hace su transaccion unica con rollback + integrity_check
    post-commit (NO copia completa de la base: correria decenas de veces
    por dia, ver su docstring)."""
    try:
        resultado = subprocess.run(
            [sys.executable, MERCADO_BONOS_REFRESH_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=MERCADO_BONOS_REFRESH_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco de mercado de bonos no termino en {MERCADO_BONOS_REFRESH_TIMEOUT_SEG}s (timeout)",
        )

    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


PRECIOS_REFRESH_SCRIPT = "importador_precios.py"
PRECIOS_REFRESH_TIMEOUT_SEG = 60  # dolarapi.com (MEP/CCL) + data912.com (paneles de acciones/CEDEARs/bonos)


@app.post("/api/refrescar-precios")
def refrescar_precios():
    """Corre importador_precios.py como subproceso (2026-09-13,
    encontrado que `precios` llevaba 9 dias sin actualizarse -- ver
    ESTADO_PWOS.md, ya le habia pasado antes el 2026-08-31). A
    diferencia de refrescar-mercado-bonos/refrescar-macro, este NUNCA
    estuvo wireado a nada -- desde que se escribio (Tarea 6, 2026-07-14)
    fue "manual bajo demanda", sin API ni cron. Usa solo fuentes
    publicas (dolarapi.com, data912.com), sin credenciales de Windows
    -- sin motivo tecnico para no automatizarlo, a diferencia de
    enriquecer_mercado_bonos.py (necesita credenciales de la API de un broker del
    Credential Manager, inaccesible desde el contenedor Linux).

    Misma EXCEPCION deliberada a "solo lectura" que el resto de los
    refrescos: UPSERT (`INSERT OR IGNORE` sobre `UNIQUE(activo_id,
    fecha)`, correr esto de nuevo el mismo dia es un no-op), con su
    propio backup + integrity_check."""
    try:
        resultado = subprocess.run(
            [sys.executable, PRECIOS_REFRESH_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=PRECIOS_REFRESH_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco de precios no termino en {PRECIOS_REFRESH_TIMEOUT_SEG}s (timeout)",
        )

    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


PUESTA_AL_DIA_SCRIPT = "puesta_al_dia.py"
PUESTA_AL_DIA_TIMEOUT_SEG = 900  # peor caso: hueco grande de renta fija (Rava, 1,5 s por ticker) + reejecucion del job macro
_puesta_al_dia_lock = threading.Lock()


@app.post("/api/puesta-al-dia")
def puesta_al_dia():
    """Puesta al dia de jobs (2026-09-24, ver puesta_al_dia.py): recupera desde la
    fuente lo que los jobs de n8n no pudieron cargar por PC apagada/suspendida.
    Lo dispara un workflow HORARIO de n8n. Idempotente: si no falta nada es un
    no-op real (sin backup, sin escritura, sin red). Devuelve el resultado en
    JSON (`rellenos`, `pendientes`, `alertas`, `errores`); 409 si ya hay una
    corrida en curso (una corrida larga no debe pisarse con la del tick siguiente).
    Un crash o timeout del script devuelve 500/504: lo captura el workflow de alertas."""
    if not _puesta_al_dia_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="ya hay una puesta al dia en curso")
    try:
        try:
            resultado = subprocess.run(
                [sys.executable, PUESTA_AL_DIA_SCRIPT], cwd=DATABASE_DIR, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=PUESTA_AL_DIA_TIMEOUT_SEG,
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail=f"la puesta al dia no termino en {PUESTA_AL_DIA_TIMEOUT_SEG}s (timeout)")
        lineas = [l for l in resultado.stdout.strip().splitlines() if l.strip()]
        if resultado.returncode != 0 or not lineas:
            raise HTTPException(status_code=500, detail={"returncode": resultado.returncode, "stderr": resultado.stderr[-2000:],
                                                          "stdout": resultado.stdout[-1000:]})
        try:
            return json.loads(lineas[-1])
        except json.JSONDecodeError:
            raise HTTPException(status_code=500, detail={"error": "la ultima linea de salida no es JSON", "stdout": resultado.stdout[-1000:]})
    finally:
        _puesta_al_dia_lock.release()


@app.get("/api/puesta-al-dia/log")
def puesta_al_dia_log(limite: int = Query(50, ge=1, le=500)):
    """Ultimas filas de la bitacora `puesta_al_dia_log` (que se relleno, de que fuente, alertas emitidas)."""
    con = _conectar()
    try:
        cols = ["id", "ejecucion", "tarea", "accion", "fecha_dato", "filas", "fuente", "detalle", "creado_en"]
        filas = con.execute(f"SELECT {', '.join(cols)} FROM puesta_al_dia_log ORDER BY id DESC LIMIT ?;", (limite,)).fetchall()
        return [dict(zip(cols, f)) for f in filas]
    finally:
        con.close()


MACRO_REFRESH_SCRIPT = "cargar_benchmarks_macro.py"
MACRO_REFRESH_TIMEOUT_SEG = 120  # xlsx del BCRA + BLS + NY Fed + cme-fedwatch + yfinance


@app.post("/api/refrescar-macro")
def refrescar_macro():
    """Corre cargar_benchmarks_macro.py como subproceso -- refresca
    `benchmarks_macro` (inflacion AR + REM, CPI EEUU, Fed, devaluacion,
    S&P 500, riesgo pais EMBI+) para el header del dashboard y el panel macro de las solapas
    de Analisis. Misma excepcion deliberada a "solo lectura" que
    /api/refrescar-mercado-bonos: UPSERT sobre una tabla de referencia
    100% derivada de fuentes externas, re-ejecutable, tolerante a fallos
    por benchmark. No necesita credenciales (todas las fuentes son
    publicas sin key) -- corre bien dentro del contenedor. Pensado para
    n8n, cadencia diaria."""
    try:
        resultado = subprocess.run(
            [sys.executable, MACRO_REFRESH_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=MACRO_REFRESH_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco macro no termino en {MACRO_REFRESH_TIMEOUT_SEG}s (timeout)",
        )
    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


ECONOMIA_REAL_SCRIPT = "cargar_indicadores_economia_real.py"
ECONOMIA_REAL_TIMEOUT_SEG = 180  # SIPA solo pesa ~29MB, + xlsx del BCRA/INDEC -- mas margen que refrescar-macro


@app.post("/api/refrescar-indicadores-economia-real")
def refrescar_indicadores_economia_real():
    """Corre cargar_indicadores_economia_real.py como subproceso --
    refresca los 5 indicadores de economia real argentina (Epica 16,
    2026-09-15: patentamientos DNRPA, construccion ISAC, empleo privado
    SIPA, mora de hogares BCRA, industria EMI) en `benchmarks_macro` +
    `macro_historico`. A diferencia de /api/refrescar-macro (diario),
    esto es cadencia MENSUAL -- pensado para un Schedule Trigger de n8n
    propio, separado del resto (los datos no cambian entre corridas del
    mismo mes, correrlo mas seguido no aporta nada)."""
    try:
        resultado = subprocess.run(
            [sys.executable, ECONOMIA_REAL_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=ECONOMIA_REAL_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco de indicadores de economia real no termino en {ECONOMIA_REAL_TIMEOUT_SEG}s (timeout)",
        )
    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


ZPINDEX_SCRIPT = "cargar_zpindex.py"
ZPINDEX_TIMEOUT_SEG = 90  # 3 PDF (~1-1.5MB c/u) + hasta 12 HEAD por region para resolver la URL vigente


@app.post("/api/refrescar-zpindex")
def refrescar_zpindex():
    """Corre cargar_zpindex.py como subproceso -- refresca el precio de
    venta USD/m2 de Zonaprop (CABA, Cordoba, Rosario) en
    `benchmarks_macro`, sin `macro_historico` (Epica 16, 2026-09-15:
    la fuente no publica serie pasada, "arranca de cero"). Cadencia
    MENSUAL -- mismo criterio que /api/refrescar-indicadores-economia-
    real, Schedule Trigger de n8n propio."""
    try:
        resultado = subprocess.run(
            [sys.executable, ZPINDEX_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=ZPINDEX_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"refresco de zpindex no termino en {ZPINDEX_TIMEOUT_SEG}s (timeout)",
        )
    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


HISTORICO_RF_SCRIPT = "cargar_mercado_renta_fija_historico.py"
HISTORICO_RF_TIMEOUT_SEG = 30  # ~890 filas pero sin red externa (lee mercado_renta_fija_rava
                                # ya cacheada + reusa rotacion_bonos in-process para el overlay de cartera)


@app.post("/api/snapshot-mercado-bonos-historico")
def snapshot_mercado_bonos_historico():
    """Corre cargar_mercado_renta_fija_historico.py como subproceso --
    snapshot DIARIO (no cada 30 min) de TIR/precio/paridad/duration de
    TODO el universo de Rava (~890 especies) + carry/BPV/breakeven solo
    para los ~22 bonos con posicion real, en `mercado_renta_fija_
    historico` (2026-09-11: comparar evolucion de TIR
    entre bonos parecidos y descubrir oportunidades fuera de cartera --
    ver create_schema.py v2.17/v2.18). Pensado para n8n con un trigger
    DIARIO NUEVO, separado del de /api/refrescar-mercado-bonos (cada 30
    min). Misma excepcion deliberada a "solo lectura" que el resto de
    los refrescos: UPSERT re-ejecutable, con su propio backup +
    integrity_check."""
    try:
        resultado = subprocess.run(
            [sys.executable, HISTORICO_RF_SCRIPT],
            cwd=DATABASE_DIR,
            capture_output=True,
            text=True,
            timeout=HISTORICO_RF_TIMEOUT_SEG,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail=f"snapshot historico de bonos no termino en {HISTORICO_RF_TIMEOUT_SEG}s (timeout)",
        )
    cuerpo = {
        "ok": resultado.returncode == 0,
        "returncode": resultado.returncode,
        "stdout": resultado.stdout,
        "stderr": resultado.stderr,
    }
    if resultado.returncode != 0:
        raise HTTPException(status_code=500, detail=cuerpo)
    return cuerpo


@app.get("/api/mercado-bonos-historico")
def mercado_bonos_historico_endpoint(ticker: str | None = None, desde: str | None = None,
                                      hasta: str | None = None, en_cartera: bool | None = None):
    """Epica 12 Parte 4 (2026-09-12): lee la serie que
    /api/snapshot-mercado-bonos-historico viene escribiendo desde el
    2026-09-11 -- hasta esta ronda no habia ningun GET para esta tabla.
    `ticker` es el uso esperado (comparar evolucion de TIR de un bono
    puntual), sin filtros devuelve TODO lo snapshoteado (crece ~898
    filas/dia, usar con cuidado)."""
    con = _conectar()
    try:
        return listar_historico_rf(con, ticker=ticker, desde=desde, hasta=hasta, en_cartera=en_cartera)
    finally:
        con.close()


@app.get("/api/bonos-ratio-universo")
def bonos_ratio_universo_endpoint():
    """Epica 14 Parte 2 (2026-09-14): universo de tickers ofrecibles en
    el comparador de ratio -- los que tienen cotizacion real en
    `precios` (no los ~890 de todo Rava, ver ratio_bonos.py). Se pide
    aparte de /api/bonos-mercado porque el universo es distinto (los
    que el usuario opero, no todo el mercado)."""
    con = _conectar()
    try:
        return tickers_con_historial(con)
    finally:
        con.close()


@app.get("/api/bonos-ratio-historico")
def bonos_ratio_historico_endpoint(ticker_a: str, ticker_b: str, desde: str | None = None):
    """Epica 14 Parte 2 (2026-09-14): serie diaria del
    ratio precio_a/precio_b entre 2 bonos cualesquiera (ej. AL30/GD30,
    ley local vs. ley NY -- el "ratio" que siguen los brokers), fuente
    `precios` (cotizacion real de Rava, ya validada contra data912 --
    ver ratio_bonos.py para por que NO se usa mercado_renta_fija_
    historico). Solo los dias donde AMBOS tickers tienen precio ese
    dia (interseccion, sin interpolar). `desde` (YYYY-MM-DD) opcional
    para acotar la ventana."""
    con = _conectar()
    try:
        return ratio_historico(con, ticker_a, ticker_b, desde)
    finally:
        con.close()


@app.get("/api/ccl-mep-brecha-historico")
def ccl_mep_brecha_historico_endpoint(desde: str | None = None, hasta: str | None = None):
    """Epica 12 Parte 4 (2026-09-12): brecha CCL/MEP dia a dia, derivada
    en vivo de las series de precios/USD_MEP y USD_CCL que PWOS ya
    tiene completas (2018-10-29 y 2013-01-02 en adelante) -- sin tabla
    nueva, ver brecha_cambiaria.py."""
    con = _conectar()
    try:
        return brecha_ccl_mep_historico(con, desde=desde, hasta=hasta)
    finally:
        con.close()


@app.get("/api/macro-historico")
def macro_historico_endpoint(clave: str, desde: str | None = None, hasta: str | None = None):
    """Epica 12 Parte 4 (2026-09-12): serie historica de un indicador
    macro puntual (riesgo_pais, inflacion_mensual, inflacion_interanual,
    rem_inflacion_12m, rem_devaluacion_esperada_12m -- ver
    migrar_v222_macro_historico.py para el enum completo). `clave` es
    obligatorio: son series de escalas muy distintas, no tiene sentido
    devolverlas mezcladas."""
    con = _conectar()
    try:
        return listar_macro_historico(con, clave=clave, desde=desde, hasta=hasta)
    finally:
        con.close()


# ------------------------------------------------------------------
# Epica 21 (2026-09-29): indicadores macro con historial, umbrales y
# objetivos. Todo sale de `indicadores_catalogo` + `macro_historico`
# (ver indicadores_macro.py); delta 3m/12m y percentil 10 anios se
# calculan al vuelo en cada GET, no se guardan.
# ------------------------------------------------------------------
INDICADORES_MACRO_SCRIPT = "cargar_indicadores_macro.py"
INDICADORES_MACRO_REFRESCO_TIMEOUT_SEG = 300   # FRED + BCRA + Excel ITCRM (~3,6 MB) + yfinance
INDICADORES_MACRO_BACKFILL_TIMEOUT_SEG = 900   # historia completa de un indicador (o de todos)


def _correr_cargador_indicadores(args: list[str], timeout: int) -> dict:
    """Corre cargar_indicadores_macro.py como subproceso (mismo patron que
    /api/refrescar-macro) y devuelve su resumen por indicador, que el
    script imprime en la linea `RESUMEN_JSON: ...`. Un fallo de UNA
    fuente no es un error del endpoint: viene como `error` en su fila."""
    try:
        resultado = subprocess.run(
            # --db explicito: la misma base que esta usando la API (mismo
            # criterio que FUNDAMENTALES_SCRIPT), nunca el default del script.
            [sys.executable, INDICADORES_MACRO_SCRIPT, "--db", str(DB_PATH), *args],
            cwd=DATABASE_DIR, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"cargar_indicadores_macro.py no termino en {timeout}s (timeout)")
    resumen = None
    for linea in resultado.stdout.splitlines():
        if linea.startswith("RESUMEN_JSON: "):
            resumen = json.loads(linea[len("RESUMEN_JSON: "):])
    if resultado.returncode != 0 or resumen is None:
        raise HTTPException(status_code=500, detail={
            "returncode": resultado.returncode, "stdout": resultado.stdout[-4000:], "stderr": resultado.stderr[-4000:],
        })
    return {
        "ok": True,
        "con_error": sum(1 for r in resumen if r.get("error")),
        "resumen": [
            {k: r.get(k) for k in ("indicador", "filas_nuevas", "filas_actualizadas", "ultima_fecha", "error")}
            for r in resumen
        ],
    }


@app.post("/api/refrescar-indicadores-macro")
def refrescar_indicadores_macro(tanda: int | None = None):
    """Trae lo nuevo de cada fuente (FRED, BCRA, ITCRM, yfinance,
    argentinadatos) y hace upsert en `macro_historico`. Pensado para n8n,
    1 vez por dia despues del cierre de NY (lo cablea el usuario). Devuelve
    {indicador, filas_nuevas, filas_actualizadas, ultima_fecha, error}
    por indicador. `tanda` opcional: solo los indicadores de esa tanda
    de la Epica 21. Si falta la API key de FRED, sus indicadores vienen
    con error "falta FRED_API_KEY" y el resto se carga igual."""
    args = ["--tanda", str(tanda)] if tanda is not None else []
    return _correr_cargador_indicadores(args, INDICADORES_MACRO_REFRESCO_TIMEOUT_SEG)


@app.post("/api/backfill-indicadores-macro")
def backfill_indicadores_macro(indicador: str | None = None):
    """Carga historica (manual, una vez): la historia completa que la
    fuente permita de verdad, nunca interpolada. Sin `indicador`, todos."""
    args = ["--backfill"] + (["--indicador", indicador] if indicador else [])
    return _correr_cargador_indicadores(args, INDICADORES_MACRO_BACKFILL_TIMEOUT_SEG)


MANTENIMIENTO_BACKUPS_SCRIPT = "mantenimiento_backups.py"
MANTENIMIENTO_BACKUPS_TIMEOUT_SEG = 600  # la 1ra corrida comprime ~170 archivos (~0,5 s c/u); despues, 1-5 por dia


@app.post("/api/mantenimiento-backups")
def mantenimiento_backups(aplicar: bool = False):
    """Compresion + retencion de los backups de pwos.db (2026-09-30, ver
    mantenimiento_backups.py para la politica). Por defecto SOLO simula;
    n8n lo llama con `?aplicar=true` 1 vez por dia. Devuelve el resumen
    (archivos, MB antes/despues, cuantos se borraron/comprimieron, errores)."""
    args = [sys.executable, MANTENIMIENTO_BACKUPS_SCRIPT] + (["--aplicar"] if aplicar else [])
    try:
        resultado = subprocess.run(args, cwd=DATABASE_DIR, capture_output=True, text=True,
                                   timeout=MANTENIMIENTO_BACKUPS_TIMEOUT_SEG)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"mantenimiento de backups no termino en {MANTENIMIENTO_BACKUPS_TIMEOUT_SEG}s")
    resumen = next((json.loads(l[len("RESUMEN_JSON: "):]) for l in resultado.stdout.splitlines()
                    if l.startswith("RESUMEN_JSON: ")), None)
    if resultado.returncode != 0 or resumen is None:
        raise HTTPException(status_code=500, detail={"returncode": resultado.returncode,
                                                     "stdout": resultado.stdout[-4000:], "stderr": resultado.stderr[-4000:]})
    if resumen["errores"]:
        raise HTTPException(status_code=500, detail=resumen)   # que n8n lo marque como fallo y mande el mail
    return resumen


@app.get("/api/rendimiento-provisorio")
def rendimiento_provisorio():
    """Tramo provisorio del mes en curso para la curva de Rendimiento: desde la
    foto del ultimo cierre del sheet hasta el ultimo dia con cierre diario
    completo, calculado con PWOS en los dos extremos (ver
    rendimiento.punto_provisorio). Aparte de /api/rendimiento-serie a
    proposito: el header (YTD, acumulado) sigue solo con cierres firmes.
    null si todavia no hay ningun dia despues de la foto."""
    con = _conectar()
    try:
        return punto_provisorio(con, precios_vivo.ahora_art().date())
    finally:
        con.close()


@app.post("/api/cierres-mensuales")
def cierres_mensuales_endpoint(aplicar: bool = False):
    """Carga incremental del cierre mensual (curva de Rendimiento, ADR-023)
    desde el Excel mas nuevo de la planilla de cierres (patron en la configuracion) en descargas/ (ver
    cierres_mensuales.py). Por defecto SOLO revisa; con `?aplicar=true` inserta
    los meses nuevos (backup + transaccion). Lo dispara el usuario a mano desde n8n
    despues de cargar el mes en el sheet y bajarlo. Si algo no cierra (mes a
    medio cargar, un mes ya cargado que cambio, falta el archivo) responde 409
    con el motivo: n8n lo marca como fallo y manda el mail."""
    try:
        return cierres_mensuales.revisar(db_path=DB_PATH, escribir=aplicar)
    except cierres_mensuales.CierresError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/config-ui")
def config_ui_endpoint():
    """Lo que el frontend toma de la configuracion del usuario: titulo del
    dashboard, nombre del usuario y abreviatura/color de cada broker. En modo
    demo, ademas, la fecha de corte de los datos (el subtitulo la muestra)."""
    out = {
        "titulo": configuracion.get("titulo", default=""),
        "nombre": configuracion.get("nombre", default=""),
        "modo": "demo" if configuracion.es_demo() else "real",
        "brokers": {n: {"abreviatura": b.get("abreviatura"), "color": b.get("color")}
                    for n, b in configuracion.brokers().items()},
    }
    if configuracion.es_demo():
        out["fecha_datos"] = configuracion.hoy().isoformat()
    return out


@app.get("/api/indicadores-catalogo")
def indicadores_catalogo_endpoint():
    """Lista de indicadores activos (id, nombre, unidad, seccion...). De
    aca saca app.js las etiquetas del selector de umbrales de tesis --
    ya no hay una lista a mano en el front."""
    con = _conectar()
    try:
        return indicadores_macro.catalogo(con)
    finally:
        con.close()


@app.get("/api/indicadores-macro")
def indicadores_macro_endpoint():
    """Una fila por indicador activo: catalogo + valor actual con su fecha
    de observacion + delta 3m/12m + percentil 10 anios + anotacion del
    usuario (umbral/objetivo/nota) + `umbral_nivel_cruzado`,
    `umbral_d3m_cruzado` y `umbral_cruzado` (el OR) + detalle extra (REM,
    Fed, MEP/CCL, acumulado de compras del BCRA)."""
    con = _conectar()
    try:
        return indicadores_macro.listar_indicadores(con)
    finally:
        con.close()


@app.get("/api/maquina-economica")
def maquina_economica_endpoint():
    """Panel "Maquina economica" (Epica 21, Tanda 4): 4 semaforos
    (productividad, ciclo corto, ciclo largo, valuacion) con el color que
    sale de los umbrales/objetivos del usuario (el peor de sus indicadores;
    sin umbral = gris), el indicador que lo define, la ultima fase escrita
    y la tesis vigente mas reciente de la bitacora de la cartera."""
    con = _conectar()
    try:
        return indicadores_macro.maquina_economica(con)
    finally:
        con.close()


class FaseMaquinaBody(BaseModel):
    fase: str
    fecha: str | None = None


@app.post("/api/maquina-economica/{semaforo}/fase")
def maquina_economica_fase(semaforo: str, body: FaseMaquinaBody):
    """Nueva revision de la "fase actual" de un semaforo (se guarda como
    fila nueva, nunca pisa la anterior)."""
    con = _conectar()
    try:
        nuevo_id = indicadores_macro.guardar_fase(con, semaforo, body.fase, body.fecha)
        con.commit()
        return {"ok": True, "id": nuevo_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


@app.get("/api/maquina-economica/{semaforo}/fases")
def maquina_economica_fases(semaforo: str):
    """Historial de fases de un semaforo, la mas reciente primero."""
    con = _conectar()
    try:
        return indicadores_macro.listar_fases(con, semaforo)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        con.close()


@app.get("/api/indicadores-macro/{indicador_id}/serie")
def indicador_macro_serie(indicador_id: str, desde: str | None = None):
    """Serie para el grafico de la tarjeta, [{fecha, valor}]."""
    con = _conectar()
    try:
        try:
            indicadores_macro.validar_indicador(con, indicador_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return [{"fecha": f, "valor": v} for f, v in indicadores_macro.leer_serie(con, indicador_id, desde=desde)]
    finally:
        con.close()


class AnotacionIndicadorBody(BaseModel):
    umbral_valor: float | None = None
    umbral_direccion: Literal["mayor_que", "menor_que"] | None = None
    objetivo_valor: float | None = None
    nota: str | None = None
    # Umbral de velocidad (v2.44), contra el delta a 3 meses.
    umbral_d3m_valor: float | None = None
    umbral_d3m_direccion: Literal["mayor_que", "menor_que"] | None = None


_CAMPOS_D3M = ("umbral_d3m_valor", "umbral_d3m_direccion")


@app.put("/api/indicadores-macro/{indicador_id}/anotacion")
def indicador_macro_anotacion(indicador_id: str, body: AnotacionIndicadorBody):
    """Guarda umbral/objetivo/nota de un indicador (reemplaza lo anterior;
    todo vacio = borra la anotacion). Cada umbral (nivel y velocidad)
    necesita valor y direccion. Si el pedido NO trae los campos del umbral
    de velocidad (ej. una pantalla vieja en cache), ese umbral se conserva;
    para borrarlo se mandan los dos en null."""
    con = _conectar()
    try:
        campos = body.model_dump()
        if not body.model_fields_set & set(_CAMPOS_D3M):
            for c in _CAMPOS_D3M:
                del campos[c]
        anotacion = indicadores_macro.guardar_anotacion(con, indicador_id, **campos)
        con.commit()
        return {"ok": True, "anotacion": anotacion}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        con.close()


# NOTA: NO hay endpoint para enriquecer_mercado_bonos.py (emisor/paridad
# por-ticker via la API de un broker). Ese script lee credenciales de Windows Credential
# Manager (keyring/WinVault) -- inaccesible desde el contenedor Linux,
# mismo limite que el cliente de la API del broker. Se corre en la PC del
# usuario (a mano o Task Scheduler), no via n8n ni el contenedor.


# Registrado despues de los endpoints /api/* a proposito: Starlette
# resuelve rutas en el orden en que se declaran, asi que un mount en
# "/" solo actua como fallback para lo que ninguna ruta anterior
# matcheo (sirve static/index.html en la raiz, sin tapar /api/*).
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
