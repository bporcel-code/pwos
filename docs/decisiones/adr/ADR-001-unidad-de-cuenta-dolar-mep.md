# ADR-001 — Unidad de cuenta: dólar MEP

**Estado:** Aceptada
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Excepciones: ADR-028.

## Contexto

El portafolio contiene activos en ARS, ARS ajustado por CER, USD cable, USD MEP y cripto en USD. Hace falta una única unidad para expresar el patrimonio total y comparar rendimientos entre categorías que de otra forma no son comparables.

## Decisión

Todo el patrimonio se expresa en **dólar MEP**. Cada tabla que guarda valores monetarios tiene una columna `_usd_mep` que es la referencia canónica.

## Alternativas descartadas

- **ARS:** no sirve como reserva de valor en Argentina. Un rendimiento del 40% en ARS puede ser una pérdida real.
- **USD cable/blue:** no es el tipo de cambio al que efectivamente se convierten los activos locales.
- **USD oficial:** irrelevante para la realidad operativa del portafolio.

## Consecuencias

- Cada cierre mensual requiere el tipo de cambio MEP del día.
- Los activos en ARS se convierten usando el MEP del día de valuación.
- Los CEDEARs se convierten usando CCL (no MEP): la diferencia es menor, pero queda registrada.
- El historial de precios debe incluir el MEP del día para reconstruir valores históricos.
