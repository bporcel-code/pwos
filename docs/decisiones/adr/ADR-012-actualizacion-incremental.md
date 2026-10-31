# ADR-012 — Actualización incremental controlada por ventana de fechas

**Estado:** Aceptada
**Fecha:** 2026-07-12
**Relacionados / Excepciones:** Relacionados: ADR-006, ADR-013, ADR-014.

## Contexto

Los importadores de brokers solo soportaban carga completa desde cero: se corrieron una vez contra el historial completo y quedaron verificados contra la cartera y el efectivo real. Para mantenerlos al día, el usuario necesita recargar periódicamente sin duplicar lo ya cargado ni reverificar todo cada vez.

Una auditoría de los identificadores de origen mostró que no son uniformes entre brokers:

| Export del broker | ID de origen por movimiento |
|---|---|
| Con número de operación en cada fila | Disponible siempre. |
| Con número de movimiento en cada fila | Disponible, pero históricamente solo se guardaba para algunas categorías. |
| Con número de boleto embebido en el texto libre | Disponible para operaciones; ausente en renta, amortización y algunos cargos. |
| Con número de ticket en el CSV | Disponible, nunca guardado en la base. |
| Sin ninguna columna de ID | No existe. |

Deduplicar solo por "ID único de origen" no es viable de forma uniforme.

## Decisión

1. **La ventana de fechas la controla el usuario, no el importador.** Al descargar cada actualización, el usuario elige el rango del export, con un solapamiento de 7 a 10 días antes de la última carga. El importador en modo incremental siempre recibe un archivo chico.
2. **El importador deduplica dentro de esa ventana** calculando un `identificador_externo` por fila:
   - **ID real del broker**, cuando existe: `"{CONTENEDOR}:{TIPO_ID}:{valor}"`.
   - **Hash de contenido** (`fecha + tipo + monto + moneda + descripcion`), cuando no: `"{CONTENEDOR}:HASH:{sha256[:16]}"`.
   - Si coincide un **ID real**, la fila se saltea automáticamente. Si coincide un **hash**, la fila **no se decide en automático**: se reporta como posible duplicado para confirmación manual. Si no coincide nada, se inserta.
3. Nueva columna `movimientos.identificador_externo` (nullable), con backfill sobre todas las filas ya cargadas usando la misma regla (protocolo de ADR-014).
4. Cada descarga se guarda en `database/descargas/YYYY-MM-DD/`; nunca se sobrescribe una anterior.

## Alternativas descartadas

- **Recargar todo desde cero cada vez:** no escala, obliga a reverificar lo ya cerrado y no es idempotente sin deduplicación. Queda como fallback manual para correcciones retroactivas del broker.
- **Que el importador calcule su corte con `MAX(fecha)`:** más complejo y obliga a adivinar el margen de solapamiento.
- **Confiar en el hash para saltear filas:** dos movimientos reales distintos pueden tener los mismos 5 campos. Saltear por hash arriesga perder una fila nueva.

## Consecuencias

- Cada importador tiene un modo incremental que reporta tres grupos antes de insertar: nuevas, ya cargadas por ID real y posibles duplicados por hash.
- El backfill toca solo la columna nueva; no modifica montos, fechas, tipos ni cuentas ya verificados.
- Los exports sin ID real dependen del hash, y más filas en la ventana significan más casos a confirmar a mano. Es un costo aceptado.
