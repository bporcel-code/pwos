# ADR-019 — Docker para los servicios de larga duración (n8n y la API)

**Estado:** Aceptada
**Fecha:** 2026-08-02 (n8n); 2026-08-10 (API)
**Relacionados / Excepciones:** Relacionados: ADR-016, ADR-018, ADR-020, ADR-022.

## Contexto

La API del dashboard arrancaba con una tarea programada de Windows y un `.bat` que abría una ventana de consola. Esa ventana se podía cerrar por accidente y el servicio no se recuperaba solo ante una caída. n8n (ADR-018) ya corría en Docker desde su instalación.

## Decisión

- La API corre en un contenedor propio (`pwos-api`), con el mismo patrón que n8n: `--restart unless-stopped`.
- **Imagen:** `python:3.x-slim`, dependencias desde `requirements.txt` (generado con `pip freeze` del entorno real) y el código copiado dentro de la imagen en el build.
- La base no va dentro de la imagen: se monta con un **bind mount** del archivo `.db`, que vive en el volumen cifrado (ADR-022).
- Desde los contenedores, la API es accesible para n8n en `host.docker.internal`.

## Alternativas descartadas

- **Tarea programada de Windows + `.bat` (lo anterior):** no sobrevive a un cierre accidental ni se reinicia sola ante una caída.

## Consecuencias

- **Todo cambio de código en la API exige reconstruir la imagen** y recrear el contenedor. Un cambio en disco no tiene efecto hasta entonces.
- Si el bind mount apunta a una ruta inexistente, Docker crea en silencio un directorio vacío y el contenedor parece sano. Lo resuelve el guard de arranque de ADR-022.
- Dentro del contenedor no hay backend de `keyring`. Lo que necesite credenciales ahí las recibe por variable de entorno, con las restricciones de ADR-020 y ADR-021.
- Docker Desktop sobre WSL2 agrega su propio modo de falla (arranque, reenvío de puertos); hay un script de recuperación de un click.
