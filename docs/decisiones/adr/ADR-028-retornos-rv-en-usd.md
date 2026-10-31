# ADR-028 — Retornos de renta variable en USD de origen (excepción a la valuación MEP)

**Estado:** Aceptada
**Fecha:** 2026-09-22
**Relacionados / Excepciones:** Excepción a ADR-001. Relacionados: ADR-027.

## Contexto

ADR-001 fija el dólar MEP como unidad de cuenta. Para medir **riesgo** (volatilidad, correlación, contribución al riesgo, frontera eficiente) eso no alcanza:

- Los precios de CEDEARs y acciones locales están en pesos. Correlacionar series en pesos mezcla "se mueven parecido" con "cargan la misma devaluación", aunque se conviertan después.
- El precio crudo de un CEDEAR cae en cada fecha ex-dividendo, y eso parece una pérdida que no existe.

## Decisión

Para los cálculos de riesgo y retorno de renta variable (no para la valuación del patrimonio):

- **CEDEARs:** se usa el precio en USD del **subyacente** en su mercado de origen, **ajustado por dividendos**.
- **Acciones locales:** se usa el precio en pesos convertido con el **MEP histórico de la fecha exacta**, sin aproximar.
- **Frecuencia semanal.**

La valuación del patrimonio sigue exactamente como dice ADR-001.

## Alternativas descartadas

- **Retornos diarios sobre el precio en pesos convertido a MEP (criterio anterior):** arrastra el ruido del tipo de cambio y las caídas falsas por dividendos.

## Consecuencias

- El precio ajustado por dividendos se usa **solo** en estos cálculos. El resto del proyecto usa el precio crudo.
- Hace falta una serie de precios del subyacente (tabla `precios_candidatas`), separada de `precios`.
- Una posición sin historia suficiente queda con confianza baja o excluida, y la interfaz lo muestra.
