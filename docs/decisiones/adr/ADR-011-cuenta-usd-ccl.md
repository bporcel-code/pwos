# ADR-011 — Cuenta `USD_CCL` como quinta cuenta estándar en contenedores broker

**Estado:** Aceptada
**Fecha:** 2026-07-10
**Relacionados / Excepciones:** Relacionados: ADR-001, ADR-007, ADR-009, ADR-014.

## Contexto

Los bonos ley extranjera (por ejemplo, GD30) pagan renta y amortización en un circuito de dólares distinto al USD de cuenta habitual: el dólar cable/CCL. No es fungible de forma directa con el USD MEP que usa el resto del sistema (ADR-001); hace falta un pase manual.

El comportamiento se confirmó en los exports de dos brokers independientes:

- Uno separa ese circuito en solapas propias del Excel de movimientos. Hasta ese momento se cargaba mezclado con el USD general.
- Otro usa dos etiquetas de moneda distintas en su hoja de efectivo en moneda extranjera (`U$S` y `DOLAR CABLE`), con eventos que transfieren fondos entre ambas.

Tratar ese dólar como USD de cuenta pierde trazabilidad: no queda registro de que el saldo tiene una restricción de fungibilidad, y el saldo "disponible" queda contaminado con fondos que en la práctica no se pueden mover libremente.

## Decisión

- Se agrega una **quinta cuenta estándar** a los contenedores `tipo='broker'`: `USD_CCL` / `disponible`, además de `ARS` y `USD` en `disponible` y `caucionado`.
- No se agrega variante `caucionado` para `USD_CCL`: no hay evidencia en ningún broker de que este circuito se use como garantía de caución.
- Se aplica a **todos** los contenedores broker, para mantener la estructura uniforme aunque no todos tengan movimientos en esa cuenta.
- No requiere cambio de schema: `cuentas.moneda` es `TEXT` libre. Es una migración de datos, no de estructura.

## Alternativas descartadas

- **Mezclarlo con el USD general (situación anterior):** no permite distinguir cuánto del saldo USD es realmente disponible y cuánto espera un pase.
- **Modelarlo como un `estado` nuevo (por ejemplo, `'ccl'`):** `estado` ya significa disponibilidad versus caución (ADR-009). Sumar otro concepto sobrecargaría la columna. La distinción CCL es de **fungibilidad de la moneda**, no de disponibilidad del saldo.

## Consecuencias

- Migración de datos con backup previo y `PRAGMA foreign_key_check` (protocolo de ADR-014).
- Revisión retroactiva de los importadores ya cargados, cada reclasificación reverificada contra la cartera y el efectivo real:
  - **Broker sin distinción de circuito en el export:** todo lo USD cae en una única cuenta y no hay datos para reclasificar. No se tocó.
  - **Broker con solapas CCL propias:** la renta del circuito se reclasificó a `USD_CCL`. El export no permite reconstruir cada pase individual, así que se agregó una transferencia agregada `USD_CCL → USD` para que el saldo de hoy cierre con el real. Los saldos intermedios históricos de `USD_CCL` son una aproximación, no el timeline real.
  - **Broker con una moneda propia para el circuito cable:** reclasificado. La suma de las filas coincide con el saldo cable real dentro del redondeo del export, sin transferencia de ajuste.
- Los importadores nuevos usan `USD_CCL` desde la carga inicial.
- El pase manual entre `USD_CCL` y `USD` se modela como una transferencia entre dos cuentas del mismo contenedor.
