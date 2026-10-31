# ADR-007 — Las tenencias pertenecen al contenedor; las cuentas son efectivo por moneda

**Estado:** Aceptada
**Fecha:** 2026-07-04
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-009, ADR-011.

## Contexto

El schema v2 forzaba las tenencias de activos dentro de una cuenta ficticia (una "cartera" inventada), para que tuvieran un lugar donde vivir.

Al revisar cómo presenta la información un broker real, se vio que "cuenta en pesos", "cuenta en dólares" y "tenencias" son tres conceptos distintos. El efectivo se subdivide por moneda; las posiciones en activos no son efectivo y no pertenecen a ninguna de esas cuentas.

## Decisión

- Una **cuenta** es una subdivisión de **efectivo por moneda** dentro de un contenedor (banco, broker, billetera).
- Las **tenencias** pertenecen directamente al **contenedor**, vía `operaciones_activos.contenedor_id`, no a una cuenta.
- Schema v2.1.

## Alternativas descartadas

- **Cuenta ficticia para las tenencias:** era un parche que no reflejaba la realidad de ningún broker y mezclaba dos conceptos distintos en la misma tabla.

## Consecuencias

- Las posiciones se calculan por contenedor y activo, sin pasar por `cuentas`.
- Se corrigió antes de cargar datos reales, así que no hizo falta migrar datos.
- `cuentas` quedó libre para crecer con dimensiones propias del efectivo: estado disponible/caucionado (ADR-009) y el circuito USD CCL (ADR-011).
- Aprendizaje técnico del cambio: SQLite no valida las referencias de una vista al crearla, solo al consultarla. Después de cualquier cambio de schema hay que consultar las vistas, no solo crearlas.
