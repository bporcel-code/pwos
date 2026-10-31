# ADR-027 — Análisis cuantitativo como diagnóstico, no como recomendación

**Estado:** Aceptada
**Fecha:** 2026-09-07 (primeras herramientas de análisis); 2026-09-23 (frontera eficiente)
**Relacionados / Excepciones:** Relacionados: ADR-028.

## Contexto

El dashboard suma herramientas de análisis: rebalanceo contra un peso objetivo, correlación, contribución al riesgo, frontera eficiente y simulador de compra. Un optimizador de cartera produce pesos "óptimos" que parecen una indicación de qué hacer, pero dependen de supuestos frágiles (retornos esperados históricos, covarianzas muestrales).

## Decisión

- Las herramientas de análisis son **diagnóstico, nunca recomendación**. Muestran la cartera actual frente a referencias; la decisión es siempre del usuario.
- **Markowitz es solo referencia matemática de fondo**, nunca un motor de decisión. La frontera eficiente muestra la cartera actual, la de **mínima varianza** (guía principal) y la de **máximo Sharpe** (referencia).
- **Tasa libre de riesgo para Sharpe: T-Bill de 3 meses de EE.UU.** (`^IRX`), en la misma unidad que los retornos (USD), convertida a la frecuencia semanal del cálculo.
- **Supuestos explícitos:** retorno esperado igual al promedio histórico, covarianza muestral, tope de peso por activo y retornos semanales que solo se anualizan para mostrar.
- Cada cálculo se valida en vivo: condiciones KKT del optimizador y carteras al azar que no deben superar a los óptimos.
- Una planilla de análisis propia del usuario sirvió solo como antecedente del método, no como verdad de referencia.

## Alternativas descartadas

- **Presentar los pesos óptimos como "cartera sugerida":** convierte un resultado sensible a los supuestos en una indicación de compra o venta.

## Consecuencias

- La interfaz muestra los supuestos (ventana, tope, semanas descartadas por falta de datos, activos excluidos) junto al resultado.
- Los cálculos se hacen al vuelo sobre las posiciones actuales; no se guardan carteras "óptimas".
- Cualquier herramienta nueva de análisis sigue el mismo criterio de diagnóstico.
