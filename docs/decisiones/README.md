# Decisiones de arquitectura (ADRs)

Un **ADR** (Architecture Decision Record) registra una decisión de diseño importante: qué se decidió, en qué contexto, qué alternativas se descartaron y qué consecuencias tiene. Sirve para que, meses después, se entienda **por qué** el sistema es como es, y no solo **cómo** es.

Cada ADR vive en su propio archivo en [`adr/`](adr/), con la forma `ADR-NNN-titulo-corto.md`.

## Reglas

- **Un ADR no se reescribe.** Si una decisión cambia, se escribe un ADR nuevo y el viejo se marca como *Superada por* o *Modificada por*, apuntando al nuevo. El texto original queda como registro histórico.
- **Los números no se reutilizan.** Aunque un ADR quede superado, su número sigue siendo suyo.
- **Estados posibles:**
  - *Aceptada:* vigente.
  - *Modificada por ADR-NNN:* sigue vigente, pero otro ADR cambió su decisión de fondo (el porqué o el rol).
  - *Superada por ADR-NNN:* ya no rige; la reemplaza otro ADR.
- **Relacionados / Excepciones** no es un estado. Si una decisión sigue vigente y otra la complementa o le define una excepción acotada, se anota en ese campo, sin cambiar el estado.
- **Plantilla:** Estado · Fecha · Relacionados / Excepciones · Contexto · Decisión · Alternativas descartadas · Consecuencias. Como máximo, alrededor de una página por ADR.
- **`docs/` es publicable:** ningún ADR lleva montos reales, nombres de personas, domicilios, links personales ni números de cuenta. Si un ejemplo necesita un número, se inventa.

## Índice

| N° | Título | Estado | Fecha |
|---|---|---|---|
| [001](adr/ADR-001-unidad-de-cuenta-dolar-mep.md) | Unidad de cuenta: dólar MEP | Aceptada | anterior a 2026-07-04 |
| [002](adr/ADR-002-sqlite-archivo-unico.md) | Base de datos: SQLite sobre archivo único | Aceptada | anterior a 2026-07-04 |
| [003](adr/ADR-003-movimientos-no-fotos.md) | Modelo basado en movimientos, no en fotos | Aceptada | anterior a 2026-07-04 |
| [004](adr/ADR-004-mundo-flujo-y-mundo-inversiones.md) | Separación entre mundo flujo y mundo inversiones | Aceptada | anterior a 2026-07-04 |
| [005](adr/ADR-005-bot-telegram-canal-de-carga.md) | Interfaz de carga: bot de Telegram como canal principal móvil | Superada por 016 y 012 | anterior a 2026-07-04 |
| [006](adr/ADR-006-importacion-de-extractos.md) | Importación de extractos como fuente de datos | Modificada por 012 y 021 | anterior a 2026-07-04 |
| [007](adr/ADR-007-tenencias-pertenecen-al-contenedor.md) | Las tenencias pertenecen al contenedor; las cuentas son efectivo por moneda | Aceptada | 2026-07-04 |
| [008](adr/ADR-008-flujos-esperados.md) | `flujos_esperados` reemplaza el calendario manual de cobros | Aceptada | 2026-07-04 |
| [009](adr/ADR-009-cauciones-como-estado-del-efectivo.md) | Cauciones como estado transitorio del efectivo | Aceptada | 2026-07-06 |
| [010](adr/ADR-010-movimientos-sin-efectivo.md) | Movimientos sin contrapartida de efectivo | Aceptada | 2026-07-08 |
| [011](adr/ADR-011-cuenta-usd-ccl.md) | Cuenta `USD_CCL` como quinta cuenta estándar en contenedores broker | Aceptada | 2026-07-10 |
| [012](adr/ADR-012-actualizacion-incremental.md) | Actualización incremental controlada por ventana de fechas | Aceptada | 2026-07-12 |
| [013](adr/ADR-013-mapas-explicitos-sobre-heuristicas.md) | Mapas explícitos en lugar de heurísticas, y frenar ante lo ambiguo | Aceptada | 2026-07-08 |
| [014](adr/ADR-014-migraciones-de-schema-versionadas.md) | Migraciones de schema versionadas, con protocolo fijo | Aceptada | 2026-07-09 |
| [015](adr/ADR-015-valorizacion-centralizada.md) | Valorización centralizada en un único módulo | Aceptada | 2026-07-14 |
| [016](adr/ADR-016-dashboard-web-fastapi.md) | Dashboard web (FastAPI + HTML/JS) como interfaz principal | Aceptada | 2026-07-29 |
| [017](adr/ADR-017-acceso-remoto-por-tailscale.md) | Acceso remoto solo por red privada (Tailscale), sin exposición pública | Aceptada | 2026-07-29 |
| [018](adr/ADR-018-n8n-como-orquestador.md) | n8n self-hosted como orquestador de tareas programadas | Aceptada | 2026-08-02 |
| [019](adr/ADR-019-docker-para-servicios.md) | Docker para los servicios de larga duración (n8n y la API) | Aceptada | 2026-08-02 |
| [020](adr/ADR-020-credenciales-en-credential-manager.md) | Credenciales en Windows Credential Manager (`keyring`), nunca en `.env` | Aceptada | 2026-08-13 |
| [021](adr/ADR-021-automatizacion-con-brokers.md) | Automatización con brokers: solo con usuario de solo lectura | Aceptada | 2026-08-19 |
| [022](adr/ADR-022-cifrado-en-reposo-veracrypt.md) | Cifrado en reposo de la base con VeraCrypt y guard de arranque | Aceptada | 2026-08-14 |
| [023](adr/ADR-023-curva-de-rendimiento-desde-cierre-mensual.md) | Curva de rendimiento histórica desde el cierre mensual medido | Aceptada | 2026-09-03 |
| [024](adr/ADR-024-contenedores-sin-export.md) | Contenedores sin export de movimientos: valor por movimiento de ajuste | Aceptada | 2026-09-06 |
| [025](adr/ADR-025-esfera-economica-desde-tipo.md) | Esfera económica derivada de `movimientos.tipo`, sin columna nueva | Aceptada | 2026-09-07 |
| [026](adr/ADR-026-co-propiedad.md) | Co-propiedad: `pct_propiedad` consistente en valor, deuda y renta | Aceptada | 2026-09-15 |
| [027](adr/ADR-027-analisis-como-diagnostico.md) | Análisis cuantitativo como diagnóstico, no como recomendación | Aceptada | 2026-09-23 |
| [028](adr/ADR-028-retornos-rv-en-usd.md) | Retornos de renta variable en USD de origen (excepción a la valuación MEP) | Aceptada | 2026-09-22 |
| [029](adr/ADR-029-splits-como-evento.md) | Splits y contrasplits como evento propio (`eventos_corporativos`) | Aceptada | 2026-10-10 |
| [030](adr/ADR-030-separacion-docs-privado.md) | Separación `docs/` / `privado/`; lo publicable sale de un repo aparte | Aceptada | 2026-10-13 |
| [031](adr/ADR-031-tir-en-unidades-comparables.md) | TIR propia en unidades comparables: real para CER, USD para dollar-linked | Aceptada | 2026-10-14 |
| [032](adr/ADR-032-contenedores-compartidos.md) | Contenedores compartidos: porcentaje de propiedad aplicado al valuar | Aceptada | 2026-10-26 |
| [033](adr/ADR-033-valuacion-en-vivo-cierre-para-historia.md) | Valuación en vivo durante la rueda, cierre diario para la historia | Aceptada | 2026-10-27 |

*Las fechas "anterior a 2026-07-04" corresponden a decisiones tomadas antes del primer commit del repo. La numeración sigue el orden en que se registraron los ADRs, no siempre la fecha exacta de la decisión.*

## Decisiones pendientes

Heredadas del registro anterior. Ya se resolvieron: CEDEARs con CCL (ADR-001), carga histórica por reconstrucción de movimientos (ADR-003), dónde corre el bot (ADR-005, superada), el tipo de cambio intradía frente al de cierre ([ADR-033](adr/ADR-033-valuacion-en-vivo-cierre-para-historia.md)) y la frecuencia de valuación de Real Estate (ver tabla).

| Pregunta | Prioridad | Estado |
|---|---|---|
| ¿Real estate se valúa mensualmente o solo cuando hay transacción? | Baja | **Resuelta.** El valor de cada propiedad se actualiza una vez por mes, en el cierre mensual, al cargar las tenencias por contenedor. Se registra en la tabla `valuaciones` (ver ADR-026). Es una práctica de carga, no una decisión de arquitectura, así que no tiene ADR propio. |
