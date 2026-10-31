# ADR-021 — Automatización con brokers: solo con usuario de solo lectura

**Estado:** Aceptada
**Fecha:** 2026-08-13 (pausa de seguridad); 2026-08-19 (decisión definitiva)
**Relacionados / Excepciones:** Relacionados: ADR-005, ADR-006, ADR-018, ADR-020.

## Contexto

Dos brokers ofrecen API. Automatizar contra ellas (por ejemplo, que n8n traiga movimientos o haga un "doble check" de tenencias) parecía el paso natural hacia el objetivo de captura automática (ADR-005). Antes de hacerlo se preguntaron dos cosas a cada broker:

1. ¿Existe un usuario de API **de solo consulta**, que no pueda operar?
2. ¿El segundo factor de autenticación protege el acceso por API?

Las respuestas fueron distintas:

- **Un broker no ofrece usuario de solo consulta**: la credencial de API tiene los mismos permisos que la cuenta completa, incluido operar. Tampoco confirmó si el segundo factor protege la API.
- **El otro sí ofrece un usuario de solo consulta.**

Además, automatizar implica que la credencial viva en algo que corre sin supervisión (un contenedor disparado por n8n). Un contenedor no puede leer Credential Manager (ADR-020), así que habría que volver a exponer la clave en texto plano.

## Decisión

- **Solo se automatiza contra un broker si la credencial es de solo lectura** y no permite operar. Con esa condición, el peor caso de una filtración queda acotado desde el diseño.
- **El broker sin usuario de solo consulta queda descartado de forma permanente para automatización.** Su cliente de API queda como herramienta manual de uso puntual: nunca se conecta a n8n, nunca corre en un contenedor y nunca queda programado.
- El broker con usuario de solo consulta es candidato a automatización. Hoy su API se usa en solo lectura para datos de mercado.
- La carga de movimientos sigue siendo por extractos (ADR-006, ADR-012).

## Alternativas descartadas

- **Automatizar con la credencial completa "porque es solo lectura en el código":** el código no acota el peor caso; la credencial filtrada sí puede operar.
- **SSH desde n8n hacia la PC para correr el cliente localmente:** le da al orquestador una terminal sobre toda la máquina (ver ADR-018).

## Consecuencias

- La decisión no se reabre salvo que ese broker ofrezca algún día un usuario de solo consulta.
- El objetivo de captura en tiempo real (ADR-005) no puede apoyarse en APIs de brokers con credencial completa.
- Los exports manuales siguen siendo la fuente de movimientos de todos los contenedores.
