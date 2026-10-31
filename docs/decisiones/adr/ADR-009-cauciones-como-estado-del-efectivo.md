# ADR-009 — Cauciones como estado transitorio del efectivo

**Estado:** Aceptada
**Fecha:** 2026-07-06 (extensión con `plazo_fijo`: 2026-09-04)
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-006, ADR-007, ADR-010, ADR-011.

## Contexto

En algunos brokers, casi todo saldo de efectivo que queda ocioso al final del día se cauciona de un día para el otro (money market manual). La gran mayoría de las cauciones son de un día; el resto son a plazo más largo, generalmente en dólares.

El propio broker muestra "Efectivo caucionado" como un renglón de saldo al mismo nivel que "Disponible pesos" / "Disponible dólares". Es decir, lo trata como un **estado del efectivo**, no como una tenencia distinta.

Con una frecuencia casi diaria, modelar cada caución como un `activo` individual ensuciaría el catálogo de `activos` con cientos de registros efímeros por año. Esa tabla está pensada para instrumentos relativamente estables (acciones, bonos, CEDEARs).

## Decisión

Las cauciones se modelan como un **estado transitorio del efectivo**, no como activos.

- `cuentas` incorpora una columna `estado` (`'disponible'` | `'caucionado'`), con `UNIQUE (contenedor_id, moneda, estado)`. Cada contenedor + moneda pasa a tener dos cuentas.
- **Apertura de caución:** movimiento `transferencia` de la cuenta `disponible` a la `caucionado` (mismo contenedor, misma moneda).
- **Liquidación:** `transferencia` inversa más un ingreso por el interés devengado, directo a `disponible`.
- No se crean activos ni posiciones nuevas.

**Canal de carga:** toda caución entra por importación de extractos (ADR-006), nunca por carga manual. El importador arma el par apertura/liquidación y el interés a partir de las filas del extracto.

## Alternativas descartadas

- **Un activo por cada caución:** descartada por volumen; ensuciaría el catálogo de `activos` de forma insostenible.
- **Dos activos genéricos (`CAUCION_ARS`, `CAUCION_USD`):** viable, pero fuerza el molde activo/posición sobre algo que en la práctica es el mismo efectivo, momentáneamente inmovilizado.

## Consecuencias

- `vista_saldos_cuenta` expone `estado`, para que dos cuentas del mismo contenedor y moneda no queden indistinguibles.
- El efectivo caucionado se suma al efectivo total sin lógica extra: sigue siendo patrimonio líquido, temporalmente inmovilizado.
- **Extensión (2026-09-04, schema v2.9):** se agrega `'plazo_fijo'` como tercer valor de `estado`, para los bancos. Es el mismo patrón económico que una caución, pero llamarlo "caucionado" sería incorrecto para un banco. Como SQLite no permite modificar un `CHECK` existente, `cuentas` se recreó completa dentro de una transacción (ver ADR-014).
- El mismo patrón (extender el modelo con un estado en vez de inventar entidades) se reusó en ADR-010.
