# ADR-024 — Contenedores sin export de movimientos: valor por movimiento de ajuste

**Estado:** Aceptada
**Fecha:** 2026-09-06
**Relacionados / Excepciones:** Excepción a ADR-003. Relacionados: ADR-006, ADR-007, ADR-026.

## Contexto

Algunos contenedores (por ejemplo, plataformas de inversión inmobiliaria fraccionada) no permiten descargar ningún resumen de movimientos. Lo único que el usuario puede ver es cuánto vale hoy su posición, y actualizarlo periódicamente a mano.

Estos contenedores **sí son activos** en sentido contable: son parte del patrimonio del usuario y tienen que contar en el patrimonio total. Lo que falta no es el valor, sino el **historial de movimientos** que ADR-003 necesita para calcular el saldo como suma de eventos.

Hay que distinguir dos usos de la palabra "activo":

- **Activo en sentido contable:** cualquier cosa de valor que forma parte del patrimonio. Estos contenedores lo son.
- **La tabla `activos`:** el catálogo de instrumentos con ticker y precio de mercado (acciones, bonos, CEDEARs), cuyas posiciones se calculan desde `operaciones_activos`.

## Decisión

Como **regla general para cualquier contenedor sin export de movimientos** (no solo para el primero que lo usó):

- El contenedor se da de alta como cualquier otro, con su cuenta (por ejemplo, `USD`/`disponible`).
- Su valor entra por **movimientos de ajuste manual**: en cada actualización, un script calcula la diferencia entre el valor que informa la plataforma y el saldo actual de la cuenta, y carga **un** movimiento por esa diferencia:
  - En la primera carga, un `ingreso` (el saldo inicial no se puede reconstruir).
  - En las siguientes, `renta` si el valor subió o `egreso` si bajó.
- El saldo sale de `movimientos`, como el de cualquier cuenta. Esta es la excepción a ADR-003: el movimiento no refleja un evento real, sino la diferencia entre dos valuaciones.

## Alternativas descartadas

- **Cargarlos en la tabla `activos` con un historial de precios:** se intentó y se deshizo. Confunde la plataforma (el contenedor) con un instrumento cotizado, y fuerza un ticker y un "precio" que no existen. Lo que se descartó fue usar la tabla `activos`, no que sean activos.

## Consecuencias

- Cuentan en el patrimonio total por el mismo camino que cualquier otra cuenta (`vista_saldos_cuenta`), sin casos especiales en el dashboard.
- No hay desglose por inversión subyacente ni ganancia por instrumento: solo un valor agregado por contenedor.
- **Los aportes y retiros reales tienen que cargarse como movimientos propios antes del ajuste.** Si no, el ajuste los registraría como renta o egreso y distorsionaría el rendimiento.
- Si el mismo contenedor también figura en `propiedades` (ADR-026), su valuación no se vuelve a sumar al patrimonio: ya está contado por su cuenta.
