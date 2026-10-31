# ADR-026 — Co-propiedad: `pct_propiedad` consistente en valor, deuda y renta

**Estado:** Aceptada
**Fecha:** 2026-09-15 (decisión); implementado del 2026-09-17 al 2026-09-19
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-008, ADR-024.

## Contexto

Un inmueble puede ser del usuario solo en parte (por ejemplo, en co-propiedad con otra persona). El patrimonio, la renta y el rendimiento tienen que reflejar **solo la parte del usuario**, pero algunos datos llegan naturalmente al 100% (una tasación es del inmueble entero) y otros ya llegan en su parte (lo que efectivamente cobra o paga).

## Decisión

Cada propiedad tiene un `pct_propiedad` en la tabla `propiedades`. Según la tabla, el dato se guarda de una de dos formas:

| Tabla | Cómo se guarda | Dónde se aplica `pct_propiedad` |
|---|---|---|
| `valuaciones` | **Al 100%** del inmueble | Al leer: `valor × pct_propiedad / 100`. Nunca se guarda ya multiplicado. |
| `alquileres` | **Neto**: la parte del usuario | No se vuelve a aplicar. |
| `gastos_propiedad` | **Neto** | No se vuelve a aplicar. |
| `pasivos` | **Neto**: `monto_original_usd` ya es la parte del usuario | No se vuelve a aplicar. |
| `propiedades.inversion_inicial_usd` | **Neto**: el capital propio puesto | El script de carga no aplica ni valida el porcentaje; lo da quien carga. |

- **Saldo de un pasivo** = `monto_original_usd` − suma de sus `pagos_pasivo`. Se calcula al leer y nunca se guarda (ADR-003). El saldo vigente de **todos** los pasivos se resta del patrimonio total.
- **Valor terminal de una propiedad** (para TIR y VPN) = `valuación × pct_propiedad / 100 − saldo vigente del pasivo vinculado`. Representa la parte propia del inmueble neta de deuda, no el valor bruto.

## Alternativas descartadas

- **Cargar el 100% del inmueble con una nota aclaratoria:** descartada por el usuario el 2026-09-15. Quería que el patrimonio total de PWOS fuera fielmente el suyo y no el del condominio, así que eligió aplicar su porcentaje de forma consistente en valor, saldo y renta.
- **Guardar todo al 100% y aplicar el porcentaje al leer en todas las tablas** (reconstruida a partir del código): los alquileres, gastos y deudas llegan ya en la parte propia. Reescalarlos obligaría a inventar un 100% que nadie informa.
- **Guardar todo neto, incluida la valuación** (reconstruida a partir del código): una tasación es del inmueble entero. Guardarla multiplicada pierde el dato original y obliga a recalcular si cambia el porcentaje.
- **Valor terminal bruto en la TIR:** con pagos de deuda chicos contra un valor bruto grande, la serie casi nunca tiene una raíz con sentido económico.

## Consecuencias

- **`valuaciones` es la única tabla al 100%.** Todo código que lea valuaciones tiene que aplicar `pct_propiedad`; todo código que lea las otras tablas no.
- Un cambio de `pct_propiedad` solo recalcula la valuación propia. Alquileres, gastos, pasivos e inversión inicial ya cargados no se ajustan solos.
- Un fondo inmobiliario que ya cuenta en el patrimonio por su cuenta de efectivo (ADR-024) no suma además su valuación.
