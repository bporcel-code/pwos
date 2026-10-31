# ADR-010 — Movimientos sin contrapartida de efectivo

**Estado:** Aceptada
**Fecha:** 2026-07-08
**Relacionados / Excepciones:** Relacionados: ADR-009, ADR-014, ADR-029.

## Contexto

Al investigar una discrepancia entre el portafolio calculado y el real de un broker, se detectó que las transferencias de títulos entre brokers y los dividendos en especie (acciones recibidas sin pago en efectivo) no mueven efectivo. Sin embargo, el schema exigía `monto > 0` y al menos una cuenta no nula en todo movimiento.

Ese supuesto (todo movimiento tiene una pata de efectivo) no se cumple en estos casos. Por eso quedaban afuera de `operaciones_activos` y las posiciones calculadas eran incorrectas.

## Decisión

- Se agrega la columna `naturaleza` (`efectivo` / `en_especie`) a `movimientos`.
- Se relaja `CHECK(monto > 0)` a `CHECK(monto >= 0)`.
- Se permite que `cuenta_origen_id` y `cuenta_destino_id` sean ambos NULL **solo** cuando `naturaleza = 'en_especie'`. Los movimientos de efectivo siguen exigiendo al menos una cuenta.

Mismo enfoque que ADR-009: extender el modelo con un flag en vez de inventar una entidad o una cuenta ficticia.

## Alternativas descartadas

- **Cuenta o monto ficticio (por ejemplo, 0,01 simbólico):** evita tocar el schema, pero ensucia los reportes de efectivo con valores sin significado real y no es auditable. No queda claro para quien lea los datos que ese monto es un artefacto técnico.

## Consecuencias

- Migración de schema v2.1 → v2.2: SQLite no permite alterar un `CHECK` existente, así que `movimientos` se recreó completa dentro de una transacción, con backup previo y `PRAGMA foreign_key_check`. Es la primera migración del protocolo descrito en ADR-014.
- Todo importador o script que inserte en `movimientos` debe decidir explícitamente la `naturaleza` (default `'efectivo'`).
- `vista_posiciones` no necesitó cambios: un `operaciones_activos` vinculado a un movimiento `en_especie` cierra la posición correctamente.
