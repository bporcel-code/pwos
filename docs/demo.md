# La demo de PWOS

PWOS se usa con datos reales y esos datos no se publican. Para poder levantar el dashboard y probarlo de punta a punta, `database/generar_demo.py` arma una base con un **inversor ficticio** cuyas decisiones son inventadas, pero en la que **cada precio, dólar, tasa, cronograma e indicador es el dato real de su fecha**.

## Quién es el inversor demo

Una persona que en enero de 2024 tiene unos USD 30.000 ahorrados y un sueldo en pesos del que ahorra todos los meses (USD 1.000 en 2024, 1.200 en 2025 y 1.400 en 2026). Opera en cuatro contenedores ficticios:

| Contenedor | Para qué |
|---|---|
| Broker A | Bonos soberanos en dólares y CEDEARs |
| Broker B | Acciones argentinas, una ON, LECAP/BONCAP (sin cauciones) |
| Banco Demo | Sueldo, gastos, plazos fijos en pesos y en dólares, cobro del alquiler, cuota del crédito |
| Billetera Demo | Gastos del día a día (no paga interés) |

## Cronología

| Fecha | Qué hace | Con qué dato real |
|---|---|---|
| 02/01/2024 | Aporta USD 30.000 y escribe la tesis de la cartera: rentas en dólares para 2028 | MEP e inflación del día |
| 04-05/01/2024 | CEDEARs SPY, KO y XOM (Broker A); acciones ALUA y TXAR (Broker B); PAMP en febrero | Cierre de BYMA y MEP de cada día |
| ene-abr 2024 | El ahorro queda en pesos, en plazo fijo a 30 días | Tasa de plazo fijo del BCRA del día (tasa real negativa) |
| desde may-2024 | El ahorro se pasa a dólares y va a plazo fijo en USD | Tasa de plazo fijo en dólares del BCRA |
| 10/07/2024 | AL30 y GD30, con tesis de compresión del riesgo país (1.477 pb) | Compra después del pago del 09/07/2024 |
| 17/07/2024 | PG y PEP (consumo defensivo) | |
| 16/12/2024 | Vende AL30 y cierra la tesis con su reflexión (riesgo país 677 pb, +45 % en dólares) | |
| 15/01/2025 | ON PN36O | Cupones de su cronograma |
| 11/04/2025 | Anota la salida del cepo en la "Máquina económica" | |
| 15/09/2025 | GD35, tesis electoral de convicción baja (riesgo país 1.231 pb) | |
| 15/10/2025 | Tesis de Real Estate: un 2 ambientes en CABA | zpindex de septiembre (USD 2.452/m²), rentabilidad bruta 4,8 % de Zonaprop |
| 28/10/2025 | Después de las legislativas vende GD30 y cierra su tesis (+82 % con cupones y amortizaciones) | |
| 12/11/2025 | LECAP S31G6 | |
| 20/11/2025 | Compra el departamento: 36 m² × zpindex de octubre (USD 2.449) más 4 % de gastos, con un crédito de USD 40.000; vende parte de los CEDEARs para completar | |
| desde dic-2025 | Alquila: arranca en $ 514.400, con ajuste trimestral por inflación; paga la cuota del crédito; ABL y mantenimiento | Alquiler de referencia de Zonaprop (2 amb., 50 m²), llevado a 36 m²; inflación del INDEC |
| 05/01/2026 | Cierra la tesis de cartera v1 con su reflexión y escribe la v2 | |
| 15/01/2026 | BONCAP T30A7 | |
| 10/03/2026 | MSFT, después de simularla | |
| 15/07/2026 | Rebalanceo: MSFT pasó a pesar un tercio de la renta variable, vende el 30 % | |
| 31/08/2026 | Cobra la S31G6 al vencimiento y cierra su tesis (+25,9 % en pesos contra 25,7 % de inflación) | |

Las fechas se mueven solas al primer día hábil con precio, y cada cambio queda anotado en `database/demo/generacion.md`. Cada mes cobra el sueldo, paga sus gastos y ahorra. Además, todo cupón, amortización y dividendo real de lo que tiene en cartera se le acredita.

Al 28/10/2026 el inversor tiene un patrimonio de unos USD 84.400: unos USD 33.000 en la cartera financiera, más el departamento (valuado con el zpindex) y menos el saldo del crédito. La curva de rendimiento acumula alrededor de +42 % desde febrero de 2024.

## Qué es real y qué es inventado

| Real (dato público de su fecha) | Inventado |
|---|---|
| Dólar MEP, CCL y A3500 de cada día | La persona, sus contenedores y su sueldo |
| Cierres de BYMA de CEDEARs, acciones, bonos, ONs y LECAPs | Qué compra, cuándo y por cuánto (en USD) |
| Cronogramas de pago de los bonos, dividendos pagados | Sus gastos, el ABL y el mantenimiento |
| Tasas de plazo fijo del BCRA, inflación, riesgo país, REM | Los textos de las tesis, umbrales, anotaciones y fases |
| zpindex y alquiler de referencia de Zonaprop | Los m² del departamento y el crédito |
| Series macro de Argentina y de EE.UU., fundamentales | La watchlist y las simulaciones |

Reglas que respeta el generador:

- **Las cantidades salen del mercado.** Cada operación se define en dólares, y la cantidad se calcula con el cierre real de ese día y el MEP real (cada 100 nominales en bonos). Si falta un dato, la operación se mueve de fecha o se saca. Nunca se inventa un precio.
- **Las tesis no saben el futuro.** Se escriben con el dato de su fecha o uno anterior. El generador registra la fecha de cada dato citado y la validación falla si alguno es posterior. Las reflexiones comparan contra lo que realmente pasó.
- **No lee datos de nadie.** Corre en modo demo, con `config/demo.json`, y no puede abrir ninguna base fuera de `database/demo/`.
- **Usa el motor.** Los datos los cargan los cargadores de PWOS. Para el resto usa las funciones de alta, `flujo_estimado`, `conciliar_flujos` y la fórmula de cierres de `cierres_mensuales`. Solo los movimientos y operaciones van por SQL, porque la única función que los escribe es la de los importadores.

## De dónde sale cada dato

Las fuentes son las de la sección "Fuentes de datos" del [README](../README.md). Los datos son de sus dueños y se usan con fines personales y educativos. Por eso la base de la demo no se versiona: se genera bajando los datos en el momento.

## Cómo generarla y levantarla

Parado en `database/`:

```
python generar_demo.py                     # corte: último día hábil cerrado
python generar_demo.py --corte 2026-10-28  # misma fecha de corte -> misma base
```

- **Salida:** `database/demo/demo.db`, junto con `generacion.md` (qué decidió el generador), `validacion.md`, `cierre_cedears.json` (foto del cierre para "¿CEDEAR en pesos o dólares?") y `demo.env`.
- **Caché:** todo lo que se baja queda en `database/demo/cache/`, y una segunda corrida con el mismo corte no vuelve a la red.
- **Tiempo:** la primera corrida baja todo el historial (ver el tiempo medido en el README); las siguientes, con la caché, son más rápidas. En modo demo no se baja FactSet (ver Limitaciones).
- **FRED:** sin `FRED_API_KEY` en el entorno, las tarjetas macro de EE.UU. que vienen de FRED quedan vacías.

Con Docker, todo en un paso desde la raíz del repositorio (genera la base dentro del contenedor y la sirve en http://localhost:8001; ver "Cómo correr la demo" en el [README](../README.md)):

```
docker compose up --build
```

Para servir una base ya generada con `generar_demo.py` (desde `database/`):

```
docker build -t pwos-demo .
docker run -d --name pwos-demo -p 8001:8000 --env-file demo/demo.env \
  -v "$PWD/demo/demo.db:/app/pwos.db" -v "$PWD/demo/cierre_cedears.json:/app/cierre_cedears.json" pwos-demo
```

Con `PWOS_MODO=demo` (lo pone `demo.env`) la API es de solo lectura: las altas, ediciones y refrescos responden 403, no hay valuación en vivo y "hoy" es la fecha de corte. El dashboard lo sabe: oculta los formularios de carga y los botones que escriben, y pone el aviso "Demo: solo lectura" en cada panel que los tenía.

## Validaciones (al final de cada corrida)

- Todos los endpoints GET responden en modo demo y sin red, y se listan los paneles vacíos.
- Coherencia: ningún saldo negativo, posiciones iguales a las operaciones, cada compra o venta de dólares con su pareja, los cobros pasados conciliados contra los estimados y la ecuación de cada cierre mensual (patrimonio = anterior + aporte + rendimiento).
- Fechas: ninguna tesis ni reflexión cita un dato posterior a su fecha, y ningún bono se compra antes de un pago que el cronograma público no trae.
- Privacidad: los textos de la base se pasan por el escáner de la publicación.
- Ortografía: los textos del inversor (tesis, reflexiones, anotaciones, fases, notas y descripciones) no tienen palabras sin tilde o sin eñe.
- Nada en vivo: ningún endpoint sale a la red (se intercepta también lo que respondería la caché), y ningún dato guardado tiene fecha posterior al corte. Los benchmarks que los cargadores traen "del día" se llevan al último valor de su serie hasta el corte, y las probabilidades de la Fed salen de las liquidaciones de CME del día de corte.

## Limitaciones

- **Es una foto a la fecha de corte.** No hay ingesta ni automatización: el log de la puesta al día queda vacío y nada se actualiza solo.
- **API de solo lectura.** Los formularios de carga no se muestran.
- **Picos de Rava.** Los archivos históricos de Rava traen glitches de una o dos ruedas (por ejemplo, AL30 a 50.000 el 04 y el 05/03/2026, con ~86.000 antes y después). El generador los saca con el mismo filtro que la base real (`limpiar_picos_rava.py`): esos días quedan sin precio, no se rellenan.
- **Datos que faltan, y por eso no se inventan:**
  - el cronograma público de Rava trae los pagos desde enero de 2025, así que los bonos se compran recién después del pago de julio de 2024;
  - el zpindex legible por texto empieza en septiembre de 2025 (los informes anteriores traen el precio como imagen) y el de diciembre de 2025 no está publicado: ese mes el departamento no se revalúa;
  - en modo demo no se baja FactSet (probar ~520 informes semanales tarda horas y la descarga suele fallar), así que el P/E forward y la prima de riesgo de acciones quedan sin dato;
  - la foto del mercado de bonos de Rava y los fundamentales de Yahoo solo existen "del día": son los del día en que se genera la demo. Si se genera después de la fecha de corte, la validación lo avisa.
- **Simplificaciones:**
  - el crédito hipotecario es en dólares y sin interés, porque el modelo de pasivos de PWOS no tiene interés: se modela en 240 cuotas de capital;
  - los dividendos de CEDEARs usan el ratio de conversión actual (SPY cambió de ratio en mayo de 2026; la serie de precios de Yahoo ya viene ajustada, así que la valuación cierra) y un factor neto de retención;
  - las operaciones no tienen comisiones;
  - el alquiler inicial es el de referencia de Zonaprop llevado a los m² del departamento.
