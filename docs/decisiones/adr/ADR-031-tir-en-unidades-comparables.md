# ADR-031 — TIR propia en unidades comparables: real para CER, USD para dollar-linked

**Estado:** Aceptada
**Fecha:** 2026-10-14
**Relacionados / Excepciones:** Relacionados: ADR-001, ADR-013, ADR-027.

## Contexto

La tabla "Bonos en tenencia" compara la TIR de entrada con la TIR de hoy de cada bono (la diferencia es la compresión) usando un motor propio sobre el cronograma de pagos que publica Rava. El motor descuenta el cronograma tal como viene, y eso solo funciona cuando el precio y los flujos están en la misma unidad. Hay dos casos en los que no lo están:

- **Bonos CER:** Rava publica el cronograma ya ajustado por el CER del día en que se descarga. Si se compara un precio de compra viejo con esos flujos, la TIR de entrada absorbe toda la inflación posterior y sale casi nominal. La TIR de hoy, a su vez, depende de que el cronograma esté recién bajado.
- **Dollar-linked:** el cronograma viene en pesos, pasados al tipo de cambio oficial (A3500) del día de la descarga, y el precio de mercado se toma de la especie en dólares.

Además, un bono recibido por canje entra con costo cero, porque los movimientos del canje no tienen efectivo (ADR-010), así que no hay precio de entrada.

## Decisión

- **Bonos CER (`activos.tipo = 'bono_cer'`): TIR real en las dos puntas.** El cronograma se divide por el CER del día de carga y cada precio (el de entrada y el de hoy) por el CER de su fecha, siempre con el rezago de 10 días hábiles que usan estos bonos. La serie CER es la del BCRA (variable 30), guardada en `macro_historico` con la clave `cer`. Si falta el CER de alguna fecha, la fila sale con motivo; no se calcula una TIR nominal en su lugar.
- **Dollar-linked: cálculo en USD.** El cronograma se divide por el tipo de cambio implícito del propio cronograma: la amortización futura total en pesos dividida por 100. Es el mismo tipo de cambio que usó Rava y no depende de que la serie A3500 esté al día. Vale solo mientras el bono no haya amortizado nada; después de una amortización la fila sale con motivo.
- **Bonos recibidos por canje: entrada representativa.** Un mapa explícito (`COSTO_ORIGEN_CANJE`, ADR-013) indica de qué bono original sale el costo. El precio de entrada es lo invertido en ese bono (al MEP de cada compra) dividido por la tenencia actual, y la fecha es la del canje. Mide "cuánto invertí contra lo que me queda por cobrar".

## Alternativas descartadas

- **Usar directamente la TIR que publica Rava:** mezcla convenciones distintas en las dos puntas de la compresión. Además, Rava no da una TIR de entrada.
- **Refrescar a diario el cronograma de los CER:** arregla solo la TIR de hoy y deja mal la de entrada.
- **Convertir el dollar-linked con la serie A3500:** depende de que esa serie esté cargada y de usar exactamente el mismo día que usó Rava. El tipo de cambio implícito sale del propio dato.
- **Reconstruir el costo exacto de la cadena de canje (cupones cobrados en el medio, relación de canje):** no hay datos suficientes. La aproximación queda documentada como tal.

## Consecuencias

- En los CER, la TIR y la compresión son reales (por encima del CER) y se marcan como tales en la tabla. No se comparan con las TIR nominales de otras filas.
- La TIR de hoy de un CER casi no depende de la antigüedad del cronograma. Queda un resto chico porque el ajuste de Rava no sigue exactamente el mismo rezago.
- Los días hábiles se cuentan de lunes a viernes, sin feriados. Un feriado corre el CER un día.
- La entrada por canje no descuenta lo cobrado del bono original antes del canje, así que la TIR de entrada queda algo por debajo de la real.
- Un bono nuevo de cualquiera de estos tipos necesita la misma revisión: tipo `bono_cer`, `moneda_pago` correcta o entrada en el mapa de canjes.
