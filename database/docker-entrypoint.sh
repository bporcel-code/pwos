#!/bin/sh
# PWOS -- guard de arranque para pwos-api (Epica 9, 2026-08-14).
#
# Motivo: confirmado empiricamente que si el bind mount de pwos.db
# apunta a una ruta del host que no existe al arrancar el contenedor
# (ej. volumen cifrado VeraCrypt todavia sin montar), Docker crea
# SILENCIOSAMENTE un directorio vacio en su lugar -- el contenedor
# queda "Up" y sano segun `docker ps`, pero cualquier request que
# toque la base devuelve 500, sin ninguna senal visible salvo mirar
# logs o abrir el dashboard.
#
# Este script corre ANTES de uvicorn (ver ENTRYPOINT en el Dockerfile)
# y valida que /app/pwos.db sea un archivo SQLite real, no un
# directorio fantasma ni un archivo vacio/corrupto. Si falla, el
# contenedor crashea con un mensaje claro en los logs en vez de
# arrancar "sano" -- combinado con --restart unless-stopped, Docker
# reintenta solo, y en cuanto el volumen cifrado se monte, el proximo
# reintento pasa el chequeo y arranca normal, sin que el usuario tenga que
# reiniciar nada a mano.
set -e

DB_PATH="/app/pwos.db"

if [ ! -e "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH no existe -- ¿esta montado el volumen cifrado?" >&2
    exit 1
fi

if [ -d "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH es un directorio, no un archivo -- Docker crea esto solo cuando el bind mount origen no existia al arrancar (volumen cifrado sin montar). Monta el volumen y el proximo reintento de --restart unless-stopped deberia arrancar solo." >&2
    exit 1
fi

if [ ! -s "$DB_PATH" ]; then
    echo "ERROR: $DB_PATH existe pero esta vacio (0 bytes) -- no es una base SQLite valida." >&2
    exit 1
fi

if ! head -c 16 "$DB_PATH" | grep -aq "SQLite format 3"; then
    echo "ERROR: $DB_PATH no tiene el encabezado esperado de un archivo SQLite -- invalido o corrupto." >&2
    exit 1
fi

echo "OK: $DB_PATH valido, arrancando."
exec "$@"
