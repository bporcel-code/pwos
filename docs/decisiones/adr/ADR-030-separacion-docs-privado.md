# ADR-030 — Separación `docs/` / `privado/`; lo publicable sale de un repo aparte

**Estado:** Aceptada
**Fecha:** 2026-10-13
**Relacionados / Excepciones:** Relacionados: ADR-002, ADR-020.

## Contexto

La documentación del proyecto mezclaba arquitectura y decisiones técnicas, que tienen valor publicable, con bitácoras y roadmap que contienen montos, nombres y datos de cuentas reales. Además, el historial de git del repo de trabajo ya contiene datos personales.

## Decisión

- **`docs/`** contiene solo documentación técnica publicable: arquitectura, decisiones (ADRs), guías y glosario.
- **`privado/`** contiene todo lo personal (bitácoras, roadmap, documentos de inmuebles) y nunca se publica.
- **Regla de `docs/`:** nunca lleva montos reales, nombres de personas, domicilios, links personales ni números de cuenta. Si un ejemplo necesita un número, se inventa.
- **El repo de trabajo nunca se hace público**, porque su historial contiene datos personales. Lo publicable se publica solo desde un **repo separado**, con historial limpio, generado por un script con **lista blanca** de archivos.

## Alternativas descartadas

- **Hacer público el repo de trabajo después de limpiar los archivos actuales:** el historial de git conserva todo lo que alguna vez se commiteó.
- **Reescribir el historial del repo de trabajo:** es destructivo y frágil, y basta un commit futuro descuidado para volver a filtrar.
- **Lista negra (publicar todo salvo lo marcado):** un archivo nuevo con datos personales se publicaría por defecto.

## Consecuencias

- Todo documento nuevo en `docs/` se escribe ya limpio, y conviene revisarlo con una búsqueda de montos, nombres y direcciones antes de commitear.
- Los links desde `docs/` hacia `privado/` quedan rotos en la versión publicada y hay que evitarlos.
- El repo público y su script de lista blanca todavía no existen.
