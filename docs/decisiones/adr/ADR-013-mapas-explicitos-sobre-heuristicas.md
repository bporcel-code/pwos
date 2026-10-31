# ADR-013 — Mapas explícitos en lugar de heurísticas, y frenar ante lo ambiguo

**Estado:** Aceptada
**Fecha:** 2026-07-08 (primer mapa); formalizada el 2026-07-14
**Relacionados / Excepciones:** Relacionados: ADR-012, ADR-015.

## Contexto

Varias clasificaciones parecen deducibles con una regla general, pero la regla tiene excepciones:

- **Especies de un mismo bono.** Un bono se opera en varias especies según el sufijo del ticker (por convención, O = pesos, D = dólar MEP, C = cable). Para unificar las tenencias hay que llevar cada especie a un ticker canónico. Una regla de sufijo rompe acciones y CEDEARs cuyo ticker termina en O o D por casualidad.
- **Moneda del precio.** En teoría, la especie D de un bono cotiza directo en USD. En la práctica hay bonos con sufijo D que cotizan en pesos, y una regla de sufijo los valuaría mal.

Una heurística que acierta "casi siempre" produce errores silenciosos: el dato queda cargado, parece razonable y nadie lo revisa.

## Decisión

- Toda clasificación con excepciones conocidas se guarda como **dato explícito por ticker**: un diccionario a mano (ticker → especie canónica, ticker → moneda del precio), con comentarios que agrupan por qué se clasificó así.
- **Regla de frenado:** si un ticker no está en el mapa, el dry run o el importador lo marca `requiere_clasificacion_manual` y **no lo inserta**. No se adivina por sufijo ni se asume un default.
- La clasificación se verifica de forma empírica: por ejemplo, un precio cercano a la par indica USD; uno de miles indica pesos.

## Alternativas descartadas

- **Regla general por sufijo:** simple, pero falla en las excepciones confirmadas y lo hace sin avisar.
- **Default ante lo desconocido (por ejemplo, asumir ARS):** fue la causa original del error de moneda; un ticker nuevo quedaba mal valuado sin señal.

## Consecuencias

- Cada ticker nuevo de un tipo ambiguo exige una clasificación manual antes de cargarse. Es fricción aceptada a cambio de no tener errores silenciosos.
- Los mapas se desactualizan. Hay que auditarlos cuando cambia el catálogo de activos: ya hubo entradas que apuntaban a tickers inexistentes y se quitaron.
- El mismo criterio (ante la ambigüedad, frenar y pedir confirmación) aparece en la deduplicación por hash de ADR-012.
