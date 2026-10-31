# ADR-002 — Base de datos: SQLite sobre archivo único

**Estado:** Aceptada
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Relacionados: ADR-022.

## Contexto

Sistema de uso personal, un único usuario, sin concurrencia. Necesita ser simple de mantener, fácil de respaldar y sin dependencias de servidor.

## Decisión

**SQLite** con un único archivo `pwos.db`. El archivo es el backup: se puede copiar y mover libremente.

## Alternativas descartadas

- **PostgreSQL:** potencia innecesaria para uso personal. Requiere un servidor corriendo y configuración sin beneficio real en este caso.
- **MongoDB / NoSQL:** los datos financieros son relacionales por naturaleza. Una compra referencia una cuenta, un activo y un precio.
- **Excel / Google Sheets:** es exactamente lo que se está reemplazando. No soporta consultas complejas, no tiene integridad referencial y no escala.

## Consecuencias

- La migración a PostgreSQL es posible en el futuro si se escala a un producto multiusuario.
- El archivo `pwos.db` nunca se versiona en Git (contiene datos personales): está en `.gitignore`.
- Los backups se hacen copiando el archivo `.db`. Dónde vive el archivo y dónde caen los backups lo define ADR-022 (cifrado en reposo).
