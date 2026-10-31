# PWOS -- imagen del backend del dashboard (api.py), Tarea 8.x (Epica 8).
#
# Reemplaza la tarea programada de Windows "PWOS - Iniciar API" +
# iniciar_api.bat: mismo patron que el contenedor de n8n (--restart
# unless-stopped), sin ventana de cmd que se pueda cerrar por accidente.
#
# pwos.db y descargas/ NO se copian a la imagen -- se montan como volumen
# en `docker run` para que el contenedor vea siempre el estado real de la
# PC (ver .dockerignore). Si se edita el codigo (api.py, dashboard.py,
# cualquier dry_run_*.py, etc.), hace falta reconstruir la imagen
# (`docker build`) y recrear el contenedor -- a diferencia de antes, ya
# no alcanza con reiniciar uvicorn a mano.
#
# Comando canonico para recrear el contenedor (no vivia documentado en
# ningun lado -- hueco real encontrado 2026-08-21: sin el mount de
# backups/, el POST /api/refrescar-bonos-rava semanal de n8n escribia su
# backup en la capa efimera del contenedor, invisible y perdido en la
# proxima recreacion. Los 3 mounts son necesarios, no opcionales):
#
#   docker build -t pwos-api .
#   docker stop pwos-api; docker rm pwos-api
#   docker run -d --name pwos-api --restart unless-stopped -p 8000:8000 ^
#     -v "<repo>\database\descargas:/app/descargas" ^
#     -v "<carpeta de la base>\pwos.db:/app/pwos.db" ^
#     -v "<carpeta de la base>\backups:/app/backups" ^
#     pwos-api
#
# Desde la Epica 21 (2026-09-29) el contenedor necesita la API key de FRED
# (indicadores macro). Dentro del contenedor Linux no hay Credential
# Manager: la clave entra como variable de entorno. Recrearlo desde
# PowerShell (no cmd), leyendo la clave de keyring a la sesion y pasando
# `-e FRED_API_KEY` SIN valor -- Docker toma el valor de la sesion, asi no
# queda escrito en el comando ni en el historial:
#
#   docker build -t pwos-api .
#   docker stop pwos-api; docker rm pwos-api
#   $env:FRED_API_KEY = & "<repo>\venv\Scripts\python.exe" -c "import keyring; print(keyring.get_password('pwos', 'fred_api_key'))"
#   docker run -d --name pwos-api --restart unless-stopped -p 8000:8000 `
#     -e FRED_API_KEY `
#     -v "<repo>\database\descargas:/app/descargas" `
#     -v "<carpeta de la base>\pwos.db:/app/pwos.db" `
#     -v "<carpeta de la base>\backups:/app/backups" `
#     pwos-api
#   Remove-Item Env:FRED_API_KEY
#
# `docker restart`/`docker start` (recuperar_servicios.ps1, arranque de la
# PC) conservan la variable: solo hace falta repetir esto al RECREAR el
# contenedor. Limitacion aceptada: `docker inspect pwos-api` muestra las
# variables de entorno -- clave gratuita, de solo lectura y de datos
# publicos, no justifica Docker secrets. Si falta, los indicadores de FRED
# devuelven "falta FRED_API_KEY" y el resto se carga igual.

FROM python:3.14-slim

WORKDIR /app

# requirements-privado.txt (solo en el repo privado): dependencias de los
# clientes de API de los brokers del usuario. Si no esta, no se instala.
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ -f requirements-privado.txt ]; then pip install --no-cache-dir -r requirements-privado.txt; fi

# Todo el codigo Python vive plano en database/ (sin subpaquetes) -- se
# copia junto para no tener que mantener una lista manual de que importa
# api.py y que invoca por subprocess (dashboard.py, valorizacion.py,
# incremental_utils.py, los dry_run_*.py y sus dependencias, como los
# importadores de cada broker). Los datos (*.db, *.xls,
# *.xlsx, *.csv, descargas/) quedan afuera via .dockerignore.
COPY *.py .
COPY static/ static/
COPY config/ config/
# Catalogo de indicadores: lo siembra sembrar_catalogos.py al armar una base
# nueva (la demo del docker-compose.yml del repo publico la genera adentro).
COPY catalogos/ catalogos/

# Guard de arranque (2026-08-14, ver docker-entrypoint.sh): valida que
# /app/pwos.db sea un archivo SQLite real antes de levantar uvicorn --
# si el bind mount origen no existia al arrancar (ej. volumen cifrado
# sin montar), Docker crea ahi un directorio vacio en silencio y el
# contenedor quedaria "Up" sirviendo 500 en todo, sin ninguna senal
# visible. Con esto, en cambio, crashea con un mensaje claro y
# --restart unless-stopped reintenta solo hasta que el volumen este.
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
