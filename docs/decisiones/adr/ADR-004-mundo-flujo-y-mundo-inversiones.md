# ADR-004 — Separación entre mundo flujo y mundo inversiones

**Estado:** Aceptada
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-025.

## Contexto

Antes de PWOS existían dos herramientas separadas: una planilla para movimientos de caja (sueldo, gastos, transferencias) y otra para posiciones de inversión. Se tocan en dos puntos: cuando se transfiere dinero a un broker y en el cierre mensual.

## Decisión

PWOS **unifica los dos mundos en una sola base de datos**, pero mantiene la distinción conceptual entre:

- **Movimientos de flujo:** sueldo, gastos, transferencias entre cuentas.
- **Operaciones de inversión:** compras, ventas, cupones, dividendos.

Una transferencia de un banco a un broker es un movimiento de flujo. La compra de un bono (por ejemplo, AL30) en ese broker es una operación de inversión. Ambas son eventos en la misma tabla central, pero con distinto tipo.

## Alternativas descartadas

- **Mantener dos bases separadas (flujo e inversiones):** reproduce el problema de origen. El punto de contacto entre ambos mundos se registraría dos veces y el patrimonio total no saldría de una sola consulta.

## Consecuencias

- El punto de contacto entre los dos mundos (transferencia a broker) se registra una sola vez.
- El cálculo de ahorro neto excluye las transferencias entre cuentas propias.
- El rendimiento puro de inversiones se calcula restando los aportes de la variación patrimonial total.
- La clasificación por esfera económica (ADR-025) se apoya en esta misma distinción de tipos.
