# ADR-008 — `flujos_esperados` reemplaza el calendario manual de cobros

**Estado:** Aceptada
**Fecha:** 2026-07-04
**Relacionados / Excepciones:** Relacionados: ADR-007, ADR-026.

## Contexto

El calendario de cobros futuros (cupones, amortizaciones, dividendos, alquileres) vivía en una planilla externa con una tabla dinámica, desconectado del resto del sistema. El schema no tenía ningún lugar para responder "¿qué me genera ingresos y cuándo?".

## Decisión

Nueva tabla **`flujos_esperados`**: cada fila es un cobro esperado a futuro de cualquier activo (incluidos los inmuebles). La vista `vista_proximos_flujos` ordena los pendientes por cercanía y calcula los días restantes. Schema v2.1.

## Alternativas descartadas

- **Seguir con la planilla externa:** el calendario quedaba desconectado de las tenencias reales y había que mantenerlo a mano por duplicado.

## Consecuencias

- El calendario de cobros pasa a estar dentro de la base, junto a las tenencias que lo generan.
- En schema v2.45 (2026-10-05) la tabla se reconstruyó como **registro de cobros estimados que después se comparan con lo real**: `activo_id` admite NULL y se agrega `propiedad_id` (para alquileres; exactamente uno de los dos), además de `contenedor_id`, `monto_estimado_usd` y `origen_estimado`. La decisión de fondo (el calendario vive en la base) no cambió; cambió el uso de la tabla.
