# ADR-022 — Cifrado en reposo de la base con VeraCrypt y guard de arranque

**Estado:** Aceptada
**Fecha:** 2026-08-14
**Relacionados / Excepciones:** Relacionados: ADR-002, ADR-019, ADR-020.

## Contexto

`pwos.db` contiene todo el patrimonio del usuario y estaba en texto plano en disco. El equipo corre Windows Home sin TPM, así que no hay BitLocker ni cifrado de dispositivo nativo.

## Decisión

- La base vive en un **contenedor de archivo VeraCrypt**, cifrado por contraseña y montado a mano como una unidad de disco.
- **Solo se mueve `pwos.db`**, no el proyecto entero: el código de la API está horneado en la imagen Docker (ADR-019) y no depende de la carpeta en disco en tiempo de ejecución.
- **Los backups de la base también se guardan dentro del volumen cifrado**, nunca en la carpeta del proyecto.
- **Guard de arranque** (`database/docker-entrypoint.sh`, ejecutado antes del servidor): verifica que el archivo montado exista, no sea un directorio, no esté vacío y tenga el encabezado de SQLite. Si el volumen no está montado, el contenedor falla con un mensaje claro en vez de quedar "sano" sobre un directorio vacío. Con `--restart unless-stopped`, Docker reintenta solo y arranca normal en cuanto el volumen se monta.

## Alternativas descartadas

- **BitLocker / cifrado de dispositivo de Windows:** no disponible sin TPM en esta edición de Windows.
- **Mover todo el proyecto (repo, entorno virtual) al volumen cifrado:** más riesgo de romper herramientas sin ganancia de seguridad real.

## Consecuencias

- Después de cada reinicio hay que montar el volumen antes de que la API funcione. Hay un script de recuperación para el orden correcto (montar, reiniciar servicios).
- Todos los scripts locales tienen que saber leer la base desde el volumen cifrado; una ruta fija a la carpeta del proyecto queda desactualizada.
- Con el volumen desmontado, la base no es legible ni por quien tenga acceso al disco.
