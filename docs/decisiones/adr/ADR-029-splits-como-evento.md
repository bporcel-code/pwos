# ADR-029 — Splits y contrasplits como evento propio (`eventos_corporativos`)

**Estado:** Aceptada
**Fecha:** 2026-10-10
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-010, ADR-014.

## Contexto

Un split o contrasplit cambia la cantidad de una tenencia sin que haya compra, venta ni efectivo. Por ejemplo, un contrasplit 3:1 convierte 3 unidades en 1. Si no se registra, la posición calculada deja de coincidir con la del broker y el precio por unidad deja de ser comparable con la historia.

## Decisión

- Nueva tabla **`eventos_corporativos`** (schema v2.48), con una fila **por activo y por contenedor**: `fecha`, `tipo` (`split` / `contrasplit`), el ratio de mercado (`ratio_viejas` / `ratio_nuevas`) y la cantidad antes y después tal como la registró el broker.
- `vista_posiciones` suma el ajuste de cantidad.
- El panel de ganancia cambia la cantidad en la fecha del evento **sin tocar el costo**. El rendimiento y la calibración de dividendos leen la misma tabla.

## Alternativas descartadas

- **Modelarlo como una venta y una recompra:** inventa una ganancia realizada y efectivo que no existieron.
- **Ajuste manual de cantidad sin evento:** pierde la trazabilidad de por qué cambió la posición (ADR-003).

## Consecuencias

- La cantidad antes y después se guarda como la registró el broker, además del ratio, para que la posición resultante siga siendo verificable contra el broker.
- Todo cálculo que dependa de cantidades históricas tiene que leer `eventos_corporativos`.
- El evento es por contenedor: el mismo activo en dos brokers registra dos eventos.
