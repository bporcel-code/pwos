# ADR-018 — n8n self-hosted como orquestador de tareas programadas

**Estado:** Aceptada
**Fecha:** 2026-08-02
**Relacionados / Excepciones:** Relacionados: ADR-016, ADR-019, ADR-021.

## Contexto

Varias tareas tienen que correr solas con una frecuencia fija: refrescar precios y datos de mercado, tomar snapshots de cartera, preparar dry runs de importación y avisar cuando algo falla. Hacía falta un orquestador con horarios, historial de ejecuciones y una interfaz para ver qué corrió y qué falló.

## Decisión

- **n8n self-hosted**, en un contenedor Docker local (ADR-019), con sus datos en un volumen persistente.
- n8n **no tiene lógica de negocio propia**: dispara endpoints de la API de PWOS (ADR-016). El cálculo vive siempre en PWOS.
- **Regla fija:** lo que requiere juicio humano se queda humano. n8n prepara (descarga, dry run, reporte, alerta), pero no inserta movimientos que necesitan confirmación.
- Los workflows se crean e importan con el CLI oficial de n8n, nunca escribiendo directo en su base interna.

## Alternativas descartadas

- **Escribir los workflows directo en la base SQLite de n8n:** riesgo de romper su consistencia interna (permisos y proyectos en varias tablas vinculadas).
- **SSH desde n8n hacia la PC para correr scripts:** le daría al orquestador una terminal sobre toda la máquina. Es la herramienta equivocada para disparar una tarea puntual.

## Consecuencias

- La PC es el servidor. Si está apagada o suspendida, los disparadores no corren a horario. Hizo falta un mecanismo de puesta al día de jobs atrasados y alertas por mail.
- Los workflows de n8n no se versionan en este repo; su configuración vive en el volumen de n8n.
- Cualquier integración nueva con brokers pasa primero por la política de ADR-021.
