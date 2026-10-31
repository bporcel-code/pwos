# ADR-006 — Importación de extractos como fuente de datos

**Estado:** Modificada por ADR-012 y ADR-021
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Relacionados: ADR-005, ADR-024.

## Contexto

Cuando se tomó esta decisión, el supuesto era que los bancos y brokers argentinos no tienen API pública y que el screen scraping es frágil, pero que todos permiten exportar extractos en PDF, CSV o Excel.

## Decisión (original)

PWOS incluye **parsers de extractos**, uno por formato de export de cada contenedor. El usuario descarga el extracto periódicamente y el parser lo importa. En la versión original, los extractos eran una **fuente secundaria**, complementaria a la carga manual por bot (ADR-005).

## Alternativas descartadas

- **Screen scraping de las webs de los contenedores:** frágil ante cualquier cambio de interfaz.

## Consecuencias

- Los extractos cubren los movimientos que no se cargaron en el momento.
- El sistema debe detectar duplicados, para no importar dos veces un movimiento ya cargado.
- Los extractos son la fuente de verdad para la conciliación contra el saldo real.

## Qué cambió

- **El rol de los extractos:** pasaron de fuente secundaria a **fuente principal** de movimientos. El bot nunca se construyó (ADR-005).
- **El porqué:** el supuesto de que "no hay APIs" resultó parcialmente falso, porque algunos brokers sí las ofrecen. Los extractos siguen siendo la vía de carga de movimientos por una decisión de seguridad, no por falta de APIs (ADR-021).
- **La deduplicación:** se resolvió con ventana de fechas elegida por el usuario e identificador externo por fila (ADR-012).
