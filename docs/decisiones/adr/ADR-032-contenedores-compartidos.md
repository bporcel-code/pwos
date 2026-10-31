# ADR-032 — Contenedores compartidos: porcentaje de propiedad aplicado al valuar

**Estado:** Aceptada
**Fecha:** 2026-10-26
**Relacionados / Excepciones:** Relacionados: ADR-015, ADR-024, ADR-026.

## Contexto

Una cuenta puede ser del usuario solo en parte. El caso que lo originó: una billetera virtual compartida con otra persona, en partes iguales, donde se junta el alquiler **completo** de dos propiedades en co-propiedad (ADR-026) y se pagan compras en conjunto. Antes, cada titular cobraba su mitad en su propia cuenta.

El resumen de esa cuenta llega al 100%: cada movimiento por el monto entero y el saldo de la app es el de la cuenta completa. El patrimonio, en cambio, tiene que contar solo la parte del usuario. Lo mismo vale para los gastos y la renta que pasan por esa cuenta.

Ya había un antecedente: una billetera donde se cobraba una parte del alquiler se cargó con el saldo dividido a mano. Esa convención se descartó porque el saldo de PWOS dejaba de coincidir con el de la app, y el cruce contra la app es el control principal de cada ronda incremental (ADR-012).

## Decisión

- **`contenedores.pct_propiedad`** (REAL, por defecto 100, entre 0 y 100 sin incluir el 0): la parte del contenedor que es del usuario. Todos los contenedores propios quedan en 100.
- **Los movimientos se cargan al 100%**, tal cual el resumen. El saldo de PWOS tiene que coincidir con el saldo real de la app.
- **El porcentaje se aplica al valuar, en un solo lugar:** `valorizacion.py` (ADR-015). `pct_propiedad_por_cuenta` es la única función que lo lee, `parte_propia` lo aplica y `convertir_efectivo_a_usd` lo recibe. Todo camino que suma efectivo, gastos o renta de un contenedor lo toma de ahí: patrimonio total, efectivo, composición, snapshot, patrimonio histórico, "Movimientos y gastos", renta cobrada, interés bancario, conciliación de cobros y el monto real de cada cobro. Ninguno repite la consulta a la columna.
- **Las vistas de saldo nativo siguen al 100%.** Sirven para cruzar contra la app; el dashboard aclara qué porcentaje cuenta en el patrimonio.
- **El alquiler que entra a una cuenta compartida** se carga como `renta` al 100%, con la categoría "Alquileres". La tabla `alquileres` sigue guardando la parte neta (ADR-026), y la conciliación compara esa parte con el estimado. El interés bancario del flujo de cobros excluye la categoría, no el contenedor: el rendimiento de los fondos de esa misma cuenta sí es interés y cuenta en su parte.

## Alternativas descartadas

- **Cargar los movimientos ya multiplicados por el porcentaje** (lo que se hizo antes con otra billetera): el saldo deja de coincidir con la app, cada ronda pierde su control principal y los montos guardados no son los del resumen.
- **Contar el 100% como activo y la parte de la otra persona como pasivo:** infla el activo y el pasivo por igual sin que haya una deuda real, obliga a mover el pasivo con cada movimiento y mezcla el concepto de deuda (ADR-026) con el de co-propiedad.
- **Aplicar el porcentaje en cada consulta que lo necesite:** repite la regla en muchos lugares, como el error que motivó ADR-015.
- **Excluir el contenedor entero del interés bancario:** dejaba afuera el rendimiento genuino de esa cuenta.

## Consecuencias

- Un contenedor nuevo compartido solo necesita su porcentaje; el código de valuación no cambia.
- Todo código nuevo que sume montos de efectivo, gastos o renta por contenedor tiene que pasar por `pct_propiedad_por_cuenta`. Si no, cuenta el 100%.
- Una transferencia entre una cuenta propia y una compartida cambia el patrimonio en la mitad del monto. Esas transferencias no se clasifican solas: se revisan caso por caso en la ronda.
- Cambiar el porcentaje de un contenedor recalcula su valor en todo el historial (el patrimonio histórico usa el porcentaje vigente), a diferencia de las tablas netas de ADR-026.
