# ADR-023 — Curva de rendimiento histórica desde el cierre mensual medido

**Estado:** Aceptada
**Fecha:** 2026-09-03
**Relacionados / Excepciones:** Excepción a ADR-003. Relacionados: ADR-001.

## Contexto

La curva de rendimiento se calculaba reconstruyendo el patrimonio de cada fecha desde el ledger de movimientos (ADR-003). Para eso hace falta el precio histórico de **todos** los instrumentos en cartera en cada fecha. Muchos bonos, ONs y letras, sobre todo los vencidos o ilíquidos, no tienen una fuente retail de precio histórico.

El resultado fue una curva casi sin puntos válidos en años enteros. Se probó una regla de tolerancia (aceptar una fecha si lo no priceado era una fracción chica del patrimonio), pero el rendimiento acumulado cambiaba de signo según la tolerancia elegida. Se descartó.

## Decisión

- La curva histórica sale de la tabla **`cierres_mensuales`**, cargada desde el registro mensual que el usuario lleva a mano: el saldo real de **todos** los contenedores el día 1 de cada mes, en USD MEP.
- Es un dato **medido** (una foto), no reconstruido. Por eso es una excepción deliberada a ADR-003, acotada a esta curva.
- **Cálculo:** rendimiento time-weighted mensual, `r_m = rendimiento_m / patrimonio_{m-1}`, encadenado de forma multiplicativa. Los aportes se tratan a fin de mes (criterio conservador).
- **Convención de fecha:** el cierre rotulado con un mes es la foto del día 1 y refleja la actividad del mes anterior.

## Alternativas descartadas

- **Seguir reconstruyendo desde el ledger con precios estimados:** demasiados supuestos sobre precios que no se pueden verificar.
- **Regla de tolerancia sobre lo no priceado:** resultados inestables según el parámetro.

## Consecuencias

- La reconstrucción desde el ledger queda para un futuro reporte de conciliación (ledger contra cierre manual, contenedor por contenedor), no para la curva.
- La calidad de la curva depende de la disciplina del registro mensual. Hace falta un proceso de carga incremental de cada cierre nuevo.
- **Regla aprendida al armarlo:** toda salida de dinero del patrimonio (incluidas las compras grandes) es un retiro, no una pérdida de inversión. La base del rendimiento mensual es el patrimonio total del mes anterior, sin restarle el aporte.

## Nota de corrección (2026-10-27)

La **convención de fecha** de arriba estaba al revés. Verificado contra el tipo de cambio diario guardado (el MEP de cada columna del registro coincide con el de principios del mes siguiente) y contra la fecha real de un gasto grande conocido: **el cierre rotulado con un mes refleja la actividad de ese mes, y la foto se toma a principios del mes siguiente.**

La decisión no cambia (la curva sigue saliendo del cierre medido). Cambia solo dónde se dibuja cada punto: el día 1 del mes siguiente al rotulado. Antes la curva quedaba corrida un mes hacia atrás; los porcentajes no cambian.

Además, la carga dejó de ser manual: un proceso lee el registro, inserta solo los meses nuevos y frena si uno ya cargado cambió. Los pagos de una deuda con plata de las cuentas que suma el registro cuentan como retiro (misma regla que las compras grandes).
