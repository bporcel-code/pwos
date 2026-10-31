"""PWOS -- entorno del generador de la demo (paso 7b).

Se importa ANTES que cualquier modulo del motor (los modulos leen la
configuracion y la ruta de la base al importarse). Deja el proceso:

- en modo demo, con config/demo.json y la base de la demo (PWOS_DB);
- encerrado: `sqlite3.connect` solo abre archivos dentro de database/demo/
  (o una base en memoria). Cualquier otra ruta -- la base real, un backup --
  levanta PermissionError. Asi la demo no puede leer datos de nadie;
- con una cache en disco de todo lo que se baja (requests y yfinance), para
  que una segunda corrida con la misma fecha de corte no vuelva a la red y de
  la misma base.
"""
import hashlib
import json
import os
import pickle
import sqlite3
from pathlib import Path

DIR_DATABASE = Path(__file__).resolve().parent
DIR_DEMO = DIR_DATABASE / "demo"
DIR_CACHE = DIR_DEMO / "cache"
DB_CONSTRUCCION = DIR_DEMO / "demo_construyendo.db"
DB_DEMO = DIR_DEMO / "demo.db"


def preparar(corte: str) -> None:
    """Variables de entorno de la demo. Llamar antes de importar el motor."""
    DIR_DEMO.mkdir(exist_ok=True)
    os.environ["PWOS_MODO"] = "demo"
    os.environ["PWOS_CONFIG"] = str(DIR_DATABASE / "config" / "demo.json")
    os.environ["PWOS_DB"] = str(DB_CONSTRUCCION)
    os.environ["PWOS_HOY"] = corte
    _encerrar_sqlite()
    _cachear_requests()
    _cachear_yfinance()


# --- encierro ---------------------------------------------------------------
_connect_original = sqlite3.connect


def _permitida(ruta) -> bool:
    if isinstance(ruta, bytes):
        ruta = ruta.decode()
    texto = str(ruta)
    if texto == ":memory:" or texto.startswith("file::memory:"):
        return True
    if texto.startswith("file:"):
        texto = texto[5:].split("?", 1)[0]
    try:
        return Path(texto).resolve().is_relative_to(DIR_DEMO.resolve())
    except (OSError, ValueError):
        return False


def _connect_encerrado(ruta, *args, **kwargs):
    if not _permitida(ruta):
        raise PermissionError(f"generar_demo: no se abre ninguna base fuera de {DIR_DEMO} (pidio {ruta!r})")
    return _connect_original(ruta, *args, **kwargs)


def _encerrar_sqlite() -> None:
    sqlite3.connect = _connect_encerrado


# --- cache de requests --------------------------------------------------------
def _clave(*partes) -> str:
    return hashlib.sha256(json.dumps(partes, sort_keys=True, default=str).encode()).hexdigest()


def _cachear_requests() -> None:
    import requests
    from requests.structures import CaseInsensitiveDict

    original = requests.sessions.Session.request
    if getattr(original, "_demo_cache", False):
        return
    carpeta = DIR_CACHE / "http"
    carpeta.mkdir(parents=True, exist_ok=True)

    def request(self, method, url, params=None, data=None, json=None, **kwargs):
        clave = _clave(method.upper(), url, sorted((params or {}).items()) if isinstance(params, dict) else params, data, json)
        meta, cuerpo = carpeta / f"{clave}.json", carpeta / f"{clave}.bin"
        if meta.exists() and cuerpo.exists():
            m = __import__("json").loads(meta.read_text(encoding="utf-8"))
            r = requests.Response()
            r.status_code, r.url, r.reason, r.encoding = m["status"], m["url"], m["reason"], m["encoding"]
            r.headers = CaseInsensitiveDict(m["headers"])
            r._content = cuerpo.read_bytes()
            return r
        r = original(self, method, url, params=params, data=data, json=json, **kwargs)
        if r.status_code in (200, 404):  # un 429 o un 5xx no se guarda: se reintenta la proxima vez
            cuerpo.write_bytes(r.content)
            meta.write_text(__import__("json").dumps({"status": r.status_code, "url": r.url, "reason": r.reason,
                                                      "encoding": r.encoding, "headers": dict(r.headers)}),
                            encoding="utf-8")
        return r

    request._demo_cache = True
    requests.sessions.Session.request = request


# --- cache de yfinance ----------------------------------------------------------
def _cachear_yfinance() -> None:
    import yfinance as yf

    if getattr(yf.Ticker, "_demo_cache", False):
        return
    original = yf.Ticker
    carpeta = DIR_CACHE / "yfinance"
    carpeta.mkdir(parents=True, exist_ok=True)
    # yfinance guarda su propia cache (zonas horarias, cookies) en un SQLite de la
    # carpeta del usuario: se la manda adentro de database/demo/ (el encierro
    # de sqlite3 no la dejaria abrir afuera).
    (DIR_CACHE / "yfinance_interna").mkdir(parents=True, exist_ok=True)
    yf.set_tz_cache_location(str(DIR_CACHE / "yfinance_interna"))

    def _cacheado(clave, calcular):
        archivo = carpeta / f"{clave}.pkl"
        if archivo.exists():
            return pickle.loads(archivo.read_bytes())
        valor = calcular()
        try:
            archivo.write_bytes(pickle.dumps(valor))
        except Exception:
            pass
        return valor

    class TickerCacheado:
        _demo_cache = True

        def __init__(self, simbolo, *args, **kwargs):
            self._simbolo, self._real = simbolo, None

        def _obj(self):
            if self._real is None:
                self._real = original(self._simbolo)
            return self._real

        def __getattr__(self, nombre):
            if nombre.startswith("_"):
                raise AttributeError(nombre)
            atributo = getattr(original, nombre, None)
            if isinstance(atributo, property):
                return _cacheado(_clave("yf", self._simbolo, nombre), lambda: getattr(self._obj(), nombre))
            if callable(atributo):
                def metodo(*args, **kwargs):
                    return _cacheado(_clave("yf", self._simbolo, nombre, args, kwargs),
                                     lambda: getattr(self._obj(), nombre)(*args, **kwargs))
                return metodo
            return getattr(self._obj(), nombre)

    yf.Ticker = TickerCacheado
