"""
PWOS -- cotizaciones EN VIVO para valuar durante la rueda (ADR-033, 2026-10-27).

Decision del usuario: el patrimonio, el dashboard y todo lo que muestra "cuanto
vale hoy" se valua con el ultimo dato disponible durante la rueda (dolar Y
precios: mezclar el dolar de hoy con el precio de ayer distorsiona todo lo que
cotiza en pesos). La historia (`precios`, rendimiento, cierres, snapshots) sigue
con el cierre diario. Este modulo NUNCA escribe en la base.

Fuentes: las mismas del cierre de las 18:05 (`dry_run_precios.py`), sin fuente
nueva -- dolarapi (MEP/CCL) + los 5 paneles de data912 = 7 llamadas en paralelo
por actualizacion, con cache de TTL_SEG. Solo se baja algo cuando alguien pide
una valuacion en horario de rueda.

Reglas (confirmadas por el usuario):
- Fuera de la rueda (L-V 11:00-17:00 ART) -> cierre, sin llamadas.
- Feriado: si la fecha de dolarapi no es la de hoy -> cierre (no hay calendario
  de feriados en el proyecto; la fuente lo dice).
- Si falla el dolar (MEP o CCL) -> TODO vuelve al cierre.
- Si falla un panel, o un activo no opero hoy (`c` vacio o 0), o su moneda no
  coincide con la del cierre guardado -> ese activo usa el cierre y se lista en
  `en_cierre`. Nunca un error ni un 0.
"""

import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests

from dry_run_precios import (
    CCL_URL,
    DATA912_BASE,
    MEP_URL,
    TODOS_LOS_PANELES,
    construir_reporte,
)

ART = ZoneInfo("America/Argentina/Buenos_Aires")
APERTURA = time(11, 0)
CIERRE = time(17, 0)
TTL_SEG = 300          # cache de 5 minutos (el usuario, 2026-10-27)
TTL_FALLA_SEG = 60     # si fallo el dolar, reintentar antes, sin martillar la fuente
TIMEOUT_SEG = 8        # una sola vez, sin reintentos: si no llega, vale el cierre

_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "estado": None}


def ahora_art() -> datetime:
    return datetime.now(ART)


def en_horario_de_rueda(ahora: datetime) -> bool:
    return ahora.weekday() < 5 and APERTURA <= ahora.time() < CIERRE


def _bajar_json(url: str):
    r = requests.get(url, timeout=TIMEOUT_SEG)
    r.raise_for_status()
    return r.json()


def bajar_fuentes() -> dict:
    """Las 7 llamadas en paralelo. Cada resultado es el JSON o la excepcion."""
    urls = {"USD_MEP": MEP_URL, "USD_CCL": CCL_URL}
    urls |= {p: f"{DATA912_BASE}/{p}" for p in TODOS_LOS_PANELES}
    with ThreadPoolExecutor(max_workers=len(urls)) as pool:
        futuros = {k: pool.submit(_bajar_json, u) for k, u in urls.items()}
    out = {}
    for k, f in futuros.items():
        try:
            out[k] = f.result()
        except Exception as e:  # noqa: BLE001 -- cualquier falla de red/JSON = sin dato en vivo
            out[k] = e
    return out


def fecha_cierre_guardado(con) -> str | None:
    """Fecha del ultimo cierre de USD_MEP. Consulta directa por el indice
    UNIQUE(activo_id, fecha): `vista_precio_actual` agrupa TODA la tabla
    `precios` (~112 mil filas, ~230 ms) y no hace falta para una sola fecha."""
    row = con.execute(
        "SELECT MAX(p.fecha) FROM precios p JOIN activos a ON a.id = p.activo_id "
        "WHERE a.ticker = 'USD_MEP';"
    ).fetchone()
    return row[0] if row else None


def _en_cierre(con, motivo: str, falla: bool, con_fecha: bool = True) -> dict:
    return {
        "modo": "cierre",
        "fecha_cierre": fecha_cierre_guardado(con) if con_fecha else None,
        "hora": None,
        "motivo": motivo,
        "falla_fuente": falla,
        "fx": {},
        "precios": {},
        "en_cierre": [],
    }


def armar_estado(con, fuentes: dict, ahora: datetime) -> dict:
    """Arma el estado en vivo a partir de lo bajado (separado de la red para testearlo)."""
    for clave in ("USD_MEP", "USD_CCL"):
        dato = fuentes.get(clave)
        if isinstance(dato, Exception) or not dato or not dato.get("venta"):
            return _en_cierre(con, f"falló el dólar en vivo ({clave}): se usa el último cierre", True)

    fecha_fx = datetime.fromisoformat(fuentes["USD_MEP"]["fechaActualizacion"].replace("Z", "+00:00"))
    fecha_fx = fecha_fx.astimezone(ART)
    if fecha_fx.date() != ahora.date():
        return _en_cierre(con, "sin rueda hoy (el dólar no tiene dato del día): se usa el último cierre", False)

    paneles, paneles_caidos = {}, []
    for p in TODOS_LOS_PANELES:
        dato = fuentes.get(p)
        if isinstance(dato, Exception) or dato is None:
            paneles[p] = {}
            paneles_caidos.append(p)
        else:
            paneles[p] = {item["symbol"]: item.get("c") for item in dato}

    moneda_cierre = dict(con.execute("SELECT activo_id, moneda FROM vista_precio_actual;").fetchall())
    precios, en_cierre = {}, []
    for f in construir_reporte(con, paneles):
        precio = f["precio"]
        if f["estado"] != "matched":
            motivo = "panel caído" if paneles_caidos else f["estado"]
        elif not precio or precio <= 0:
            motivo = "sin operaciones hoy"
        elif moneda_cierre.get(f["activo_id"]) not in (None, f["moneda_precio"]):
            motivo = "moneda distinta a la del cierre"
        else:
            precios[f["activo_id"]] = (float(precio), f["moneda_precio"])
            continue
        en_cierre.append({"ticker": f["ticker"], "motivo": motivo})

    return {
        "modo": "vivo",
        "fecha_cierre": fecha_cierre_guardado(con),
        "hora": ahora.strftime("%H:%M"),
        "hora_dolar": fecha_fx.strftime("%H:%M"),
        "motivo": None,
        "falla_fuente": bool(en_cierre),
        "paneles_caidos": paneles_caidos,
        "fx": {"USD_MEP": float(fuentes["USD_MEP"]["venta"]), "USD_CCL": float(fuentes["USD_CCL"]["venta"])},
        "precios": precios,
        "en_cierre": en_cierre,
    }


def estado(con, ahora: datetime | None = None) -> dict:
    """Estado de las cotizaciones para valuar AHORA. En rueda usa el cache
    (TTL_SEG; TTL_FALLA_SEG si fallo el dolar); el refresco va bajo lock para
    que dos pedidos simultaneos no dupliquen las 7 llamadas."""
    ahora = ahora or ahora_art()
    if not en_horario_de_rueda(ahora):
        # Sin tocar la base: esto se llama por CADA precio y tipo de cambio de
        # una valuacion (~100 veces por pedido). La fecha del cierre, si hace
        # falta, la completa resumen_para_pantalla una sola vez. (Arreglo
        # 2026-10-28: consultar la fecha aca sumaba ~23 s a /api/resumen.)
        return _en_cierre(con, "fuera del horario de rueda", False, con_fecha=False)
    with _lock:
        est, ts = _cache["estado"], _cache["ts"]
        ttl = TTL_FALLA_SEG if (est and est["modo"] == "cierre" and est["falla_fuente"]) else TTL_SEG
        if est is None or _time.monotonic() - ts > ttl:
            est = armar_estado(con, bajar_fuentes(), ahora)
            _cache.update(estado=est, ts=_time.monotonic())
        return est


def limpiar_cache() -> None:
    with _lock:
        _cache.update(estado=None, ts=0.0)


def resumen_para_pantalla(est: dict, con=None) -> dict:
    """Lo que el dashboard muestra: de cuando es el dato y que cayo al cierre."""
    fecha = est.get("fecha_cierre")
    if fecha is None and con is not None:
        fecha = fecha_cierre_guardado(con)
    return {
        "modo": est["modo"],
        "hora": est.get("hora"),
        "fecha_cierre": fecha,
        "motivo": est.get("motivo"),
        "falla_fuente": est.get("falla_fuente", False),
        "en_cierre": est.get("en_cierre", []),
    }
