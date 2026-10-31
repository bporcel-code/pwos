# ADR-020 — Credenciales en Windows Credential Manager (`keyring`), nunca en `.env`

**Estado:** Aceptada
**Fecha:** 2026-08-13
**Relacionados / Excepciones:** Relacionados: ADR-019, ADR-021, ADR-022.

## Contexto

Las credenciales de las APIs de brokers vivían en un archivo `.env`. Aun con permisos NTFS restringidos, un `.env` es texto plano legible con cualquier editor por quien acceda al archivo.

## Decisión

- Las credenciales se guardan en **Windows Credential Manager**, accedido desde Python con la librería `keyring`, bajo el servicio `"pwos"`. Credential Manager cifra en reposo y está atado a la sesión de usuario de Windows.
- Se verifica que `keyring` use el backend real de Windows y no un fallback en texto plano.
- La migración de los valores la corre el usuario en su máquina; los secretos nunca pasan por el repo.
- Al repo va solo `database/.env.example`, con placeholders vacíos; `.env` está en `.gitignore`. (El repo público no lo lleva: la única clave que usa la demo, `FRED_API_KEY`, es opcional y está en el README.)

## Alternativas descartadas

- **`.env` con permisos NTFS restringidos:** sigue siendo texto plano en disco.

## Consecuencias

- Los scripts locales leen las credenciales de `keyring`.
- **Un contenedor Linux no puede leer Credential Manager.** Lo que corre en Docker (ADR-019) solo puede recibir secretos por variable de entorno, y eso vuelve a exponerlos en texto plano dentro del contenedor. Esta limitación es una de las razones de ADR-021.
- Las claves no sensibles de servicios de datos públicos siguen el mismo esquema: variable de entorno si existe y, si no, `keyring`.
