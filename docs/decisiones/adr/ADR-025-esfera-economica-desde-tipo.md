# ADR-025 — Esfera económica derivada de `movimientos.tipo`, sin columna nueva

**Estado:** Aceptada
**Fecha:** 2026-09-07
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-004.

## Contexto

Para armar secciones como "Movimientos y gastos" hace falta clasificar cada movimiento por **esfera económica**: consumo, rebalanceo, inversión o renta pasiva. La primera idea era agregar una columna y migrar el histórico.

## Decisión

- La esfera **se deriva de `movimientos.tipo`**, que ya la clasifica desde el día uno (ADR-004). El mapeo `tipo → esfera` vive en un módulo (`database/esferas.py`) y se verificó contra todos los movimientos reales de la base, sin ningún `tipo` sin mapear.
- **No se agrega columna ni migración** sobre el histórico.
- El árbol de `categorias` no duplica lo que ya resuelve `tipo`. Por ejemplo, "Inversiones" no es una categoría de egreso: las compras y ventas de activos ya se clasifican por `tipo` y por el tipo de activo.

## Alternativas descartadas

- **Columna `esfera` en `movimientos`:** duplica información que ya está en `tipo`, con riesgo de que ambas diverjan, y obliga a migrar el histórico.
- **Categoría manual "Inversiones" bajo egresos:** redundante, y un reporte que agrupe por categoría mezclaría esferas.

## Consecuencias

- La calidad de la esfera depende de que `tipo` esté bien asignado en origen. La auditoría completa de ingresos y egresos encontró movimientos mal tipados por algunos importadores (por ejemplo, interés de caución cargado como `ingreso` en vez de `renta`). Se corrigieron en el dato, no con filtros aparte.
- Cada `tipo` nuevo exige actualizar el mapeo.
- Se detectó y corrigió una diferencia entre la documentación y el `CHECK` real de `categorias.tipo` (`neutro` contra `ambos`), con migración (ADR-014).
