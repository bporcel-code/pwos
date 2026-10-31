"""Entorno de los tests publicos (CI y verificacion antes de publicar): sin red
y sin la base real. Python carga este modulo solo al arrancar si su carpeta
esta en PYTHONPATH (ver .github/workflows/tests.yml); el codigo de PWOS no lo
importa nunca.

- Cualquier conexion de red falla en el acto: un test publico no puede
  depender de una fuente externa.
- Cualquier apertura de una ruta en V: (el volumen cifrado de la base real)
  falla: un test publico no puede necesitar datos privados.
"""
import builtins
import io
import re
import socket
import sqlite3

_EN_V = re.compile(r"(?i)^(file:)?/*v:[\\/]")


def _sin_red(*args, **kwargs):
    raise OSError("tests publicos: la red esta bloqueada")


def _revisar(ruta):
    if isinstance(ruta, (str, bytes)) or hasattr(ruta, "__fspath__"):
        texto = ruta.decode() if isinstance(ruta, bytes) else str(ruta)
        if _EN_V.match(texto):
            raise PermissionError(f"tests publicos: no se puede abrir la base real ({texto})")


def _envolver(original):
    def envuelta(ruta, *args, **kwargs):
        _revisar(ruta)
        return original(ruta, *args, **kwargs)
    return envuelta


socket.socket.connect = _sin_red
socket.socket.connect_ex = _sin_red
socket.create_connection = _sin_red
sqlite3.connect = _envolver(sqlite3.connect)
builtins.open = _envolver(builtins.open)
io.open = _envolver(io.open)
