"""PWOS -- configuracion del usuario (paso 7a2 de la publicacion).

Lo que es propio de quien usa PWOS no vive en el codigo sino en un JSON:
que brokers tiene y con que formato leer la renta de cada uno, las
excepciones por ticker (moneda de la cotizacion, moneda de pago, emisor),
los patrones de texto de sus transferencias propias y de sus fondeos, la
planilla de cierres mensuales y el titulo del dashboard.

Variables de entorno:
- `PWOS_CONFIG`: ruta al JSON. Sin la variable se usa `config/usuario.json`
  si existe y, si no, `config/demo.json`; en modo demo, siempre demo.json.
- `PWOS_MODO`: "real" (default) o "demo". En modo demo la API es de solo
  lectura, la valuacion en vivo queda apagada y "hoy" es la fecha de
  generacion de la demo (`demo.fecha_generacion` del JSON, o `PWOS_HOY`).
- `PWOS_DB`: ruta a la base; pisa a la del JSON.

Cada modulo lee sus valores una sola vez, al importarse, con `get()`.
"""
import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).parent
DIR_CONFIG = DIR / "config"
_DB_LOCAL = DIR / "pwos.db"  # el bind mount de Docker


def ruta_config() -> Path:
    """`PWOS_CONFIG` si esta. Si no: en modo demo SIEMPRE config/demo.json
    (aunque haya un usuario.json al lado, la demo nunca muestra la
    configuracion real); en modo real, usuario.json si existe."""
    if os.environ.get("PWOS_CONFIG"):
        return Path(os.environ["PWOS_CONFIG"])
    usuario = DIR_CONFIG / "usuario.json"
    if es_demo() or not usuario.exists():
        return DIR_CONFIG / "demo.json"
    return usuario


@lru_cache(maxsize=1)
def cargar() -> dict:
    return json.loads(ruta_config().read_text(encoding="utf-8"))


def get(*claves, default=None):
    """`get("bonos", "moneda_pago", default={})`: valor anidado o `default`."""
    valor = cargar()
    for clave in claves:
        if not isinstance(valor, dict) or clave not in valor:
            return default
        valor = valor[clave]
    return valor


def es_demo() -> bool:
    return os.environ.get("PWOS_MODO", "real").strip().lower() == "demo"


def hoy() -> date:
    """`date.today()`, salvo en modo demo: la fecha de generacion de la demo."""
    if es_demo():
        fijo = os.environ.get("PWOS_HOY") or get("demo", "fecha_generacion")
        if fijo:
            return date.fromisoformat(fijo)
    return date.today()


def ruta_db() -> Path:
    """`PWOS_DB` si esta; si no, el pwos.db junto al codigo (bind mount de
    Docker) cuando no esta vacio; si no, `db_path` del JSON."""
    if os.environ.get("PWOS_DB"):
        return Path(os.environ["PWOS_DB"])
    if _DB_LOCAL.exists() and _DB_LOCAL.stat().st_size > 0:
        return _DB_LOCAL
    return Path(get("db_path", default=str(_DB_LOCAL)))


def ruta_backups() -> Path:
    """backups/ junto al codigo si la base es la local (Docker: los dos son
    bind mounts); si no, `backups_dir` del JSON."""
    if _DB_LOCAL.exists() and _DB_LOCAL.stat().st_size > 0:
        return DIR / "backups"
    return Path(get("backups_dir", default=str(DIR / "backups")))


def brokers() -> dict:
    """{nombre de contenedor: {abreviatura, color, formato_renta, patrones_caucion, ...}}"""
    return get("brokers", default={})
