# ADR-003 — Modelo basado en movimientos, no en fotos

**Estado:** Aceptada
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Excepciones: ADR-023, ADR-024.

## Contexto

El sistema original (una planilla) cargaba posiciones a mano cada mes: una "foto" del estado actual. Eso genera varios problemas: el dato es tan bueno como la disciplina de quien lo carga, no hay trazabilidad de por qué cambió una posición y se mezcla rendimiento con aportes.

## Decisión

**El sistema modela eventos (movimientos), no estados (fotos).** Los saldos y posiciones se calculan siempre como la suma de todos los movimientos históricos. Nunca se carga un saldo a mano.

## Alternativas descartadas

- **Modelo de fotos mensuales:** simple de implementar, pero reproduce el problema original. Sin trazabilidad y sin capacidad de responder "¿cuándo compré esto?" o "¿cuánto ahorré en marzo?".

## Consecuencias

- Requiere registrar cada movimiento relevante, no solo el estado final.
- Hace falta una vía de carga con poca fricción; si no, el modelo de movimientos se vuelve tedioso y se abandona (ver ADR-005, ADR-006 y ADR-012).
- La carga inicial de datos históricos es más compleja: hay que reconstruir los movimientos pasados o tomar una "foto inicial" como punto de partida y registrar movimientos desde esa fecha.
- Los saldos cuadran con la realidad si los movimientos están completos. Una diferencia entre el sistema y la realidad indica movimientos faltantes.
- Excepciones deliberadas: la curva de rendimiento histórica sale de cierres mensuales medidos (ADR-023) y los contenedores sin export de movimientos entran por ajuste de valuación (ADR-024).
