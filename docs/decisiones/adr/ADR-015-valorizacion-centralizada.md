# ADR-015 — Valorización centralizada en un único módulo

**Estado:** Aceptada
**Fecha:** 2026-07-14
**Relacionados / Excepciones:** Relacionados: ADR-001, ADR-013, ADR-016.

## Contexto

Los bonos, ONs y letras argentinos no se cotizan todos de la misma forma: según la fuente, el precio se informa por cada 100 nominales (lámina de VN100) o por 1 nominal. El cálculo de valorización multiplicaba cantidad × precio sin tener en cuenta esa diferencia, y la renta fija salió muy inflada en el tablero.

Al buscar el origen se vio que el cálculo `cantidad × precio` estaba escrito inline en el script del tablero. Cualquier interfaz nueva (la API web o un bot futuro) habría tenido que repetirlo y podía reintroducir el mismo error en otro lugar.

## Decisión

**Toda la valuación (cantidad × precio × tipo de cambio → USD MEP) se calcula en un único módulo, `database/valorizacion.py`**, y el resto del código lo llama. La regla por tipo de activo (por ejemplo, dividir por 100 los instrumentos cotizados por lámina) vive solo ahí.

## Alternativas descartadas

- **Corregir el cálculo inline donde apareció:** arregla el síntoma, pero deja abierta la puerta a que la próxima interfaz reimplemente el cálculo con el error original.

## Consecuencias

- Las interfaces (tablero de terminal, API web) reusan las funciones de valorización y no tienen cálculo de patrimonio propio.
- Un cambio en cómo se valúa un tipo de activo se hace en un solo lugar.
- Cada interfaz nueva debe verificarse contra las existentes: los mismos números en los mismos endpoints.
