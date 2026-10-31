# ADR-014 — Migraciones de schema versionadas, con protocolo fijo

**Estado:** Aceptada
**Fecha:** 2026-07-09 (primera migración, v2.1 → v2.2)
**Relacionados / Excepciones:** Relacionados: ADR-002, ADR-010.

## Contexto

La base contiene datos reales ya verificados contra los saldos de cada contenedor. Un cambio de schema mal aplicado puede romper integridad referencial o alterar datos verificados sin que se note.

Además, SQLite no permite modificar un `CHECK` existente con `ALTER TABLE`. Muchos cambios obligan a recrear la tabla completa.

## Decisión

Todo cambio de schema sobre la base real se hace con un **script de migración propio**, `database/migrar_vXYZ_descripcion.py`, que sigue siempre el mismo protocolo:

1. **Backup previo verificado** del archivo `.db`.
2. **Ensayo sobre una copia** (`--db <ruta>`) antes de correr contra la base real.
3. **Una única transacción.** Si hay que cambiar un `CHECK`, la tabla se recrea dentro de esa transacción.
4. **Verificación final:** `PRAGMA foreign_key_check` y `PRAGMA integrity_check` limpios, y conteos de filas iguales antes y después donde corresponde.
5. La versión nueva queda registrada en `create_schema.py` (para bases nuevas) y en la documentación de estado del proyecto.

## Alternativas descartadas

- **Cambios ad hoc desde un cliente SQL:** no dejan registro reproducible ni garantizan backup y verificación.
- **Recrear la base desde cero y reimportar:** obliga a reverificar todo lo ya cerrado contra los saldos reales (ver ADR-012).

## Consecuencias

- Cada versión del schema tiene un script que la produce, en orden.
- Las migraciones de datos (sin cambio de estructura) siguen el mismo protocolo (por ejemplo, ADR-011).
- **Hoy la versión del schema vive solo por convención** (nombre del script y documentación); la base no sabe en qué versión está. Mejora posible, no implementada: usar `PRAGMA user_version` de SQLite, el mecanismo nativo para guardar un entero de versión dentro del propio archivo. Cada migración lo leería para verificar el punto de partida y lo actualizaría al terminar.
