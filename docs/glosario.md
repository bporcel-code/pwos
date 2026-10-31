---
título: Glosario PWOS
tipo: referencia
estado: activo
creado: 2026-07-09
actualizado: 2026-10-30
tags: [pwos, glosario, referencia]
---

# Glosario PWOS

> Vocabulario preciso del sistema. Cuando haya duda sobre el significado de un término, este documento es la referencia.

---

## Términos del dominio

### Backend / Frontend / Endpoint

- **Backend**: la parte del programa que corre en un servidor y hace el
  trabajo (leer la base, calcular, decidir qué responder). No tiene
  pantalla propia.
- **Frontend**: la parte visual, la página que se ve en el navegador —
  le pide datos al backend y los muestra.
- **Endpoint**: una dirección puntual dentro del backend que responde a
  un pedido específico (ej. `/patrimonio` devuelve el patrimonio total).

### HTML / CSS / JS

- **HTML**: define la estructura de una página web (qué hay y en qué
  orden). El esqueleto.
- **CSS**: define cómo se ve (colores, tamaños, posición). La ropa y el
  maquillaje.
- **JS (JavaScript)**: define el comportamiento (qué pasa al hacer
  clic, actualizar datos sin recargar la página). Le da vida a lo que
  HTML y CSS dibujaron quietos.

### localhost

Forma de decirle a una computadora "conectate con algo que corre en vos
misma". Un backend que solo escucha en `localhost` únicamente es
visible desde esa misma máquina — ni siquiera otro dispositivo en la
misma red Tailscale podría acceder, hace falta que escuche en `0.0.0.0`
o en la IP de esa red.

### Snapshot

Captura del estado de algo en un momento puntual (ej. "snapshot de
patrimonio": el número de hoy con su fecha). Guardar snapshots
periódicos es lo que permite después armar gráficos de evolución en el
tiempo — sin ellos, solo se tiene el valor actual, no el historial.

**ADR (Architecture Decision Record)**
Documento que registra una decisión de arquitectura: qué se decidió, por qué, qué alternativas se consideraron y cuáles son las consecuencias. Ver [decisiones de arquitectura](decisiones/README.md).

**Aporte**
Capital que ingresa al portfolio desde el mundo exterior (sueldo, alquiler cobrado, ingreso extraordinario). Distinto del rendimiento — el aporte es lo que vos ponés, el rendimiento es lo que ganaron los activos.

**Caución**
Operación de préstamo de corto plazo garantizada con activos (similar a un repo). En PWOS no se modela como una tabla separada: se representa con una columna `estado` en la tabla `cuentas` (`disponible` / `caucionado`). Ver [ADR-009](decisiones/adr/ADR-009-cauciones-como-estado-del-efectivo.md).

**CCL (Contado con Liquidación)**
Tipo de cambio implícito que surge de comprar un activo en pesos en Argentina y venderlo en dólares en el exterior. Es la referencia para CEDEARs.

**CEDEAR**
Certificado de Depósito Argentino. Representan acciones de empresas extranjeras (Apple, Google, etc.) que cotizan en BYMA en pesos. Su precio sigue al CCL.

**CER (Coeficiente de Estabilización de Referencia)**
Índice que ajusta el capital de un instrumento según la inflación. Los bonos "CER-linked" actualizan su valor nominal por este coeficiente. Lo publica el BCRA a diario (base 2-2-2002 = 1). Un bono CER usa el valor de 10 días hábiles antes de cada fecha (el *rezago*). En PWOS la serie vive en `macro_historico` (clave `cer`), se muestra como tarjeta en los indicadores macro y sirve para calcular la *TIR real*. Ver ADR-031.

**A3500 (Comunicación A 3500)**
Tipo de cambio mayorista de referencia que publica el BCRA cada día hábil. Es el que se usa para liquidar los bonos dollar-linked. No es el dólar MEP ni el oficial minorista. En PWOS es el activo `USD_A3500`, con la serie diaria del BCRA desde 2002.

**Dollar-linked**
Bono que se ajusta por el tipo de cambio oficial (A3500 del BCRA) pero paga en pesos: el monto en dólares de cada pago se convierte a pesos con el oficial de ese momento. En PWOS su `moneda_pago` es `USD_LINKED`. Ver ADR-031.

**Cierre mensual**
Foto calculada del patrimonio al último día hábil de cada mes. Nunca se carga a mano — se calcula a partir de movimientos y precios.

**Contenedor**
Cualquier institución donde puede residir capital: banco, broker, exchange, billetera virtual, plataforma de crowdfunding. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**Contenedor compartido**
Contenedor que es del usuario solo en parte, por ejemplo una cuenta en partes iguales con otra persona. Sus movimientos se cargan al 100%, tal cual el resumen, para que el saldo coincida con la app; la parte propia se aplica al valuar (ver *pct_propiedad (contenedor)*). Ver [ADR-032](decisiones/adr/ADR-032-contenedores-compartidos.md).

**Cuenta**
Subdivisión dentro de un contenedor que maneja una única moneda. Tiene un saldo calculable en todo momento. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**Dólar MEP (Mercado Electrónico de Pagos)**
Tipo de cambio que surge de comprar un bono en pesos y venderlo en dólares dentro de Argentina, sin sacar divisas del país. Es la unidad de cuenta de PWOS.

**Egreso**
Movimiento donde el capital sale del sistema hacia el mundo exterior (gasto, impuesto, extracción de efectivo). El patrimonio total disminuye.

**Flujo esperado**
Registro de un cobro futuro previsto (cupón de bono, dividendo, renta). Reemplaza el calendario manual de cobros que se llevaba antes a mano. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**Ingreso**
Movimiento donde el capital entra al sistema desde el mundo exterior (sueldo, alquiler, dividendo). El patrimonio total aumenta.

**LECAP**
Letra de Capitalización del Tesoro. Bono de corto plazo en pesos que capitaliza intereses (no paga cupones periódicos). Referencia de tasa libre de riesgo en pesos.

**MEP**
Ver *Dólar MEP*.

**Movimiento**
Evento atómico que modifica el saldo de una o más cuentas. La unidad fundamental del sistema. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**ON (Obligación Negociable)**
Bono corporativo emitido por empresas privadas (YPF, TGS, Pampa, etc.). Pagan en dólares o pesos según emisión.

**Operación sobre activo**
Movimiento específico de compra o venta de un instrumento financiero. Extiende al movimiento con datos del activo: cantidad, precio, comisión.

**pct_propiedad (contenedor)**
Columna de `contenedores`: qué porcentaje del contenedor es del usuario (100 por defecto; 50 en una cuenta compartida en partes iguales). Se aplica en un solo lugar, `valorizacion.py`, a todo lo que suma efectivo, gastos o renta del contenedor. Las vistas de saldo nativo, que se cruzan contra la app, siguen al 100%. No confundir con `propiedades.pct_propiedad`, que es la parte de un inmueble (ADR-026). Ver [ADR-032](decisiones/adr/ADR-032-contenedores-compartidos.md).

**Posición**
Cantidad de un activo que tenés en una cuenta en un momento dado. Siempre calculada, nunca cargada a mano. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**Rendimiento**
Variación del patrimonio que NO es consecuencia de aportes propios — es lo que ganaron o perdieron los activos por movimiento de precios.

`Rendimiento = Patrimonio(fin) - Patrimonio(inicio) - Aportes(período)`

**Ticker**
Código único que identifica un instrumento financiero. "GGAL" para Grupo Galicia, "AL30" para el bono soberano 2030, "BTC" para Bitcoin.

**TIR (Tasa Interna de Retorno)**
La tasa anual que hace que el valor actual de los pagos futuros de un bono sea igual a su precio. En "Bonos en tenencia", la *TIR de entrada* usa el precio que se pagó y la *TIR de hoy* usa el precio actual. La diferencia entre las dos es la *compresión*.

**TIR real**
TIR por encima de la inflación. En un bono CER se obtiene expresando el precio y los pagos en unidades de CER. Por ejemplo, una TIR real del 3% quiere decir "CER + 3% anual". No se compara directamente con la TIR nominal de un bono en pesos a tasa fija. Ver ADR-031.

**Transferencia**
Movimiento donde el capital se mueve entre dos cuentas propias. No es ingreso ni egreso — el patrimonio total no cambia. Ejemplo: mandar plata del banco al broker.

**Valuación**
Valor en USD MEP de una posición en un momento dado. `Valuación = Posición × Precio_mercado / Tipo_cambio_MEP`

**VN (Valor Nominal)**
Unidad de medida de los bonos. Cuando comprás 10.000 VN de AL30, tenés derecho a los cupones e intereses calculados sobre ese nominal.

---

## Términos técnicos

**Backfill**
Corregir o completar datos **hacia atrás** en el historial ya cargado, no solo de acá en adelante. Se usa cuando se encuentra un bug o un dato faltante en el importador/clasificador: primero se arregla el código para que las cargas nuevas salgan bien, y después se "rellena hacia atrás" aplicando la misma corrección a lo que ya estaba insertado, para que todo el historial quede consistente. Ejemplos típicos: corregir el emparejamiento de las cauciones de un broker, reclasificar dividendos cobrados en dólar cable, o separar comisión y precio en compras históricas. Regla del proyecto: nunca reprocesar los CSV/XLS completos para hacer un backfill (riesgo de re-disparar bugs no relacionados) — usar siempre un script acotado que solo toque las filas puntuales afectadas.
Segundo uso del término: cargar de una sola vez la historia pasada de un dato que recién empezás a guardar, en vez de esperar a que se acumule día a día. Ej. (Épica 21, 2026-09-29): los indicadores de FRED se cargaron con 10+ años de historia el mismo día. La zpindex de Zonaprop no se puede backfillear porque la fuente no publica meses viejos.

**DDL (Data Definition Language)**
Subconjunto de SQL para definir estructuras: `CREATE TABLE`, `ALTER TABLE`, `DROP TABLE`.

**FK (Foreign Key / Clave Foránea)**
Columna que referencia la clave primaria de otra tabla. Garantiza integridad referencial — no podés insertar un movimiento con una cuenta que no existe.
Ojo: SQLite, por razones históricas, **no hace cumplir** las FK salvo que cada conexión lo pida con `PRAGMA foreign_keys = ON`. Code encontró (Épica 21, 2026-09-29) que `api.py` no lo pide, así que ahí las FK son decorativas. Quedó como deuda técnica: activarlas de golpe podría romper escrituras que hoy funcionan de casualidad.

**Hardening**
Literalmente "endurecer" — el trabajo de hacer un sistema más resistente a fallar, sin agregarle funciones nuevas. No es sumar algo, es cerrar grietas que ya existen en el código antes de que causen un problema real (ej. un identificador compartido entre movimientos, una fila que se pierde en silencio si un valor da negativo). Es el "Endurecimiento" de la Épica 8 — la fase de hacer que lo que ya funciona sea más sólido, en vez de expandir a brokers o funciones nuevas.

**Heurística**
Regla práctica que da una buena aproximación a la respuesta correcta, pero sin garantía matemática de acertar siempre — a diferencia de un identificador único y confiable. Ej: usar fecha+tipo+monto+descripción para identificar una fila cuando no hay un número de operación real; funciona la mayoría de las veces, pero puede fallar si dos eventos distintos comparten esos mismos datos.

**Markdown**
Formato de texto plano con símbolos simples (`#` para títulos, `**` para negrita, `-` para listas) usado en todos los archivos `.md` del proyecto (`ESTADO_PWOS.md`, el glosario, los ADRs). Liviano, se lee bien tanto en texto plano como renderizado (Obsidian, GitHub, VS Code), estándar de facto para documentación técnica.

**Merge**
Combinar dos conjuntos de datos en uno, cruzándolos por una columna en común — operación típica de Python/pandas cuando la información está repartida en dos tablas que hay que unir. Ej: juntar la tabla de aperturas de caución con la de liquidaciones, cruzando por el número de movimiento compartido.

**Modelo de dominio**
El conjunto de conceptos y sus relaciones que describen el problema que el sistema resuelve. Ver [modelo de dominio](arquitectura/modelo-de-dominio.md).

**PARA (Projects, Areas, Resources, Archive)**
Metodología de organización del conocimiento de Tiago Forte. La estructura del vault de Obsidian se basa en ella.

**PK (Primary Key / Clave Primaria)**
Identificador único de cada fila en una tabla. En PWOS usamos `INTEGER PRIMARY KEY AUTOINCREMENT`.

**Trigger**
Una acción automática que la base de datos ejecuta sola cuando pasa algo puntual en una tabla — por ejemplo "cada vez que se borra una fila de `movimientos`, actualizá también esta otra tabla". Es código que vive dentro de la base, no algo que vos llamás a mano. Si una tabla tiene triggers y la recreás sin volver a crearlos, esas acciones automáticas dejan de pasar sin que te des cuenta — por eso al migrar `movimientos` había que confirmar si tenía alguno.

**UTF-8**
Sistema estándar para representar texto digital, con soporte para tildes, ñ, y símbolos de otros idiomas. Si un archivo se muestra con caracteres raros (ej. `D�lares`) en la consola, no siempre significa que el dato esté mal guardado — puede ser solo un problema de cómo la consola lo muestra, mientras el archivo real está codificado correctamente en UTF-8.

**Regex (expresión regular)**
Patrón de texto para buscar o extraer partes específicas dentro de una cadena, con reglas más flexibles que una búsqueda literal. Ej: un regex que busca el patrón "Boleto / número / COMPRA" en una descripción y extrae solo el número, sin escribir a mano cada variante posible de esa frase.

**Schema**
La estructura completa de la base de datos: qué tablas existen, qué columnas tienen, qué restricciones aplican.

**SQLite**
Motor de base de datos que guarda todo en un único archivo. No requiere servidor. Ideal para aplicaciones de usuario único. Ver [ADR-002](decisiones/adr/ADR-002-sqlite-archivo-unico.md).

**Constraint SQL / CHECK**
Regla que la base de datos misma hace cumplir sobre lo que se guarda, un nivel más abajo que Python. Un `CHECK` le dice a una columna "solo aceptás estos valores" y rechaza cualquier otro, sin importar qué programa escriba. Es una segunda línea de defensa. Historia real: en Épica 15 el `CHECK` de `tesis_umbrales.indicador` tenía su propia lista y rechazaba indicadores que la pantalla dejaba elegir; hubo que reconstruir la tabla (SQLite no permite modificar un `CHECK`). En Épica 21 (2026-09-29) se eliminó: con 35 indicadores y más por venir, la validación pasó a Python contra `indicadores_catalogo`. Intercambio consciente: se pierde la defensa en la base a cambio de no duplicar la lista.

**CRUD**
Las 4 operaciones básicas sobre datos: **C**reate (crear), **R**ead (leer), **U**pdate (actualizar), **D**elete (borrar). "CRUD completo" significa que están las cuatro. Sirve para detectar huecos: sin Update, una tesis nunca se podría cerrar con su reflexión final. Las bitácoras de tesis de PWOS son CRUD completo.

**Fuente única de verdad / Catálogo**
Principio de que cada dato viva en un solo lugar y todo lo demás lo consulte ahí, en vez de tener su propia copia. Un **catálogo** es una tabla de referencia que describe qué cosas existen (no sus valores día a día). Ej. (Épica 21): la lista de indicadores válidos estaba copiada 4 veces (`CHECK`, dos listas en Python, una en `app.js`); se reemplazaron por `indicadores_catalogo`. Este mismo glosario es el ejemplo: existía otra copia en el proyecto de Claude, y este archivo quedó como el oficial.

**Imagen y contenedor (Docker)**
La **imagen** es la receta congelada: Linux mínimo, Python, librerías y el código de PWOS. El **contenedor** es esa receta funcionando. El código queda *copiado adentro* de la imagen al construirla (`docker build`), por eso cuando cambia `api.py` hay que reconstruir la imagen y recrear el contenedor (`docker stop` + `docker rm` + `docker run`): reiniciar no alcanza.

**NULL / NaN**
Dos formas de decir "no hay dato". `NULL` es el vacío de SQL; `NaN` ("Not a Number") es el vacío numérico de Python/pandas. Un vacío no es un cero: si se guarda como valor, rompe promedios, percentiles y gráficos. Por eso en Épica 21 la regla es que un NULL/NaN nunca pisa un valor existente.

**Percentil**
Qué porcentaje de los valores históricos queda por debajo del valor actual. Percentil 90 = hoy está más alto que el 90% de lo visto en la ventana (en PWOS, 10 años). Convierte un número suelto en algo interpretable ("¿es mucho o poco para este indicador?"). Trampa: solo sirve si la serie es comparable en el tiempo; el Merval en pesos nominales siempre da alto por la inflación.

**Test unitario**
Pedacito de código que prueba automáticamente una función con datos inventados cuyo resultado ya se conoce (ej. "con [1,2,3,4,5] y valor actual 5, el percentil tiene que dar 100"). Si alguien rompe la función más adelante, el test falla antes de que el error llegue a la pantalla. Como el banco de pruebas de una ECU. La función de Δ/percentil de Épica 21 tiene 23 tests.

**Upsert (`INSERT OR IGNORE` vs `ON CONFLICT DO UPDATE`)**
Insertar una fila y, si ya existe una con la misma clave, decidir qué hacer. `INSERT OR IGNORE` conserva la vieja y descarta la nueva en silencio; `ON CONFLICT DO UPDATE` pisa la vieja. Importa porque las fuentes oficiales **revisan** datos pasados (FRED corrige el desempleo de meses anteriores): con IGNORE te quedás con la primera versión para siempre. Ojo: `INSERT OR IGNORE` también silencia violaciones de `CHECK`, no solo de claves repetidas. `macro_historico` pasó a `DO UPDATE` en Épica 21, con protección contra vacíos y reporte de cuántas filas existentes cambiaron.

**Variable de entorno**
Par nombre=valor que un programa recibe del sistema al arrancar, por fuera del código. En PowerShell: `$env:NOMBRE = "valor"` y `Remove-Item Env:NOMBRE`. En Docker: `-e NOMBRE` (sin valor, Docker lo copia de la sesión). Es la forma estándar de pasarle un secreto a un contenedor. Ej. (2026-09-29): la clave de FRED vive cifrada en el Administrador de credenciales de Windows (`keyring`), se pasa a `pwos-api` como `FRED_API_KEY` al recrearlo y se borra de la sesión.

**Volumen / bind mount (`-v`)**
"Enchufa" un archivo o carpeta de la PC dentro del contenedor: `-v "<carpeta de la base>\pwos.db:/app/pwos.db"` hace que el contenedor vea y escriba la base real. Es la clave del diseño: el código vive en la imagen (descartable) y los datos en la PC (permanente), así que el contenedor se puede borrar sin perder nada. `pwos-api` usa 3 (`pwos.db`, `backups/`, `descargas/`).

**CI (integración continua)**
Un robot que corre los tests automáticamente en cada push y marca el commit con ✓ o ✗, sin que nadie tenga que acordarse de correrlos. En GitHub se llama **GitHub Actions**. En el repo público de PWOS corre los tests públicos en modo demo, sin red y sin la base real.

**Docker Compose**
Un archivo (`docker-compose.yml`) que describe cómo levantar uno o varios contenedores juntos, con sus volúmenes y puertos, para arrancarlos con un solo comando: `docker compose up`. La demo pública lo usa: un contenedor genera la base y otro la sirve.

**Email noreply de GitHub**
Dirección que GitHub le da a cada cuenta (del tipo `NNNN+usuario@users.noreply.github.com`) para firmar commits sin exponer el email personal. Todo commit lleva el email del autor y queda visible para siempre en el historial público: por eso el repo público se firma con esta y nunca con la personal.

**Fork**
Copia de un repositorio dentro de la cuenta de GitHub de otra persona. GitHub la permite en cualquier repo público, pero no da permiso de uso: lo que se puede hacer con el código lo dice la licencia.

**Hosting**
Alquilar un servidor (propio o en la nube) para que una aplicación quede funcionando online, accesible desde cualquier lado, en vez de correr solo en la PC propia.

**Lista blanca (whitelist)**
Lista de lo único permitido: lo que no figura queda afuera. Es lo opuesto a una **lista negra**, que enumera lo prohibido y deja pasar todo lo demás. La publicación de PWOS usa lista blanca: un archivo nuevo no se publica hasta que alguien lo agrega a propósito.

**Prueba de regresión**
Comparar los resultados antes y después de un cambio para confirmar que no se rompió nada de lo que ya andaba. En PWOS: guardar el JSON de los endpoints de la API sobre una copia fija de la base, hacer el cambio, volver a guardarlo y comparar; tienen que dar idénticos (o cambiar solo donde se esperaba).

**Staging**
Carpeta intermedia donde se arma y se prueba lo que se va a publicar, antes de publicarlo. En PWOS, `publicar.py` exporta ahí la lista blanca, la escanea, corre los tests y genera la demo; solo si todo pasa la copia al repo público.

### Feed (de cotizaciones)

Mecanismo que consulta repetidamente una fuente de datos externa para mantener información actualizada — en PWOS, el proceso que le pregunta a `dolarapi.com` (USD MEP) y `data912.com` (instrumentos BYMA) los precios del día y los carga en la tabla `precios`. Reemplaza el `IMPORTHTML` que el usuario usaba antes en Google Sheets para el mismo propósito.

---

## Términos de herramientas (VS Code / Claude Code)

**@-mention**
Forma de referenciar un archivo puntual en el chat de Claude Code escribiendo `@` seguido del nombre. Le indica que lea ese archivo directamente en vez de tener que buscarlo por su cuenta.

**Agent (o "Task")**
Cuando Claude Code delega una tarea completa a una sub-instancia de sí mismo, que trabaja de forma más autónoma en un objetivo específico (por ejemplo, "auditá estos 3 archivos y traeme un resumen") y vuelve con el resultado final, sin mostrar cada paso intermedio de esa investigación. Útil para tareas de exploración larga donde ver cada micro-paso sería más ruido que ayuda.

**Brief / Ticket**
Documento corto que dice qué construir, por qué, de dónde sale cada dato y qué queda fuera del alcance. Es el puente entre la charla (Cowork) y la implementación (Code). No garantiza que todo salga perfecto — quien implementa tiene que verificar. Ej.: en Épica 15 Code comprobó contra histórico real que dividir `^TNX` por 10 era un error del brief; en Épica 21 encontró que el ticket asumía que no había historial, cuando `macro_historico` ya existía.

**Bash / comando de terminal**
Comando de línea de comandos. Cuando Claude Code pide confirmación tipo "Allow this bash command?", está pidiendo permiso antes de ejecutar algo en la terminal.

**Dry run ("corrida en seco")**
Modo de ejecutar un script que simula todo el proceso pero no escribe nada real a la base — muestra qué haría (qué filas insertaría, con qué valores) sin tocar los datos. Sirve para revisar el resultado antes de dar el OK a la carga real. Es la versión automatizada de mirar una tabla de matches antes de aprobarla a mano.

**MCP (Model Context Protocol)**
Forma estándar de conectar una IA a una fuente de datos o herramienta externa para que la consulte en vivo, en vez de trabajar con archivos pegados. Diferencia: un archivo es una foto fija que queda vieja; un conector MCP es una ventana a los datos actuales. Caso PWOS (2026-09-20): que el proyecto "inversiones" vea la cartera sin pasársela a mano. La contra: el servidor tendría que ser alcanzable desde internet (Tailscale no alcanza), así que es también una decisión de seguridad.

**Pipeline**
Secuencia de pasos donde la salida de uno alimenta al siguiente, como una línea de producción. En PWOS: los 5 scripts de importación de un broker (leer → limpiar → clasificar → separar → cargar), en vez de un solo script que hace todo. Facilita ubicar en qué paso está un error.

**SELECT / INSERT / UPDATE / DELETE**
Comandos SQL básicos. `SELECT` solo lee datos (seguro de aprobar en Claude Code sin pensarlo mucho). `INSERT`, `UPDATE` y `DELETE` modifican la base — conviene revisarlos antes de confirmar.

**Frontmatter**
El bloque de metadatos al principio de una nota de Obsidian, entre `---` y `---` (como el que tiene este mismo archivo arriba de todo: título, tipo, estado, fechas, tags). No es texto que se lea como contenido — es información sobre la nota que Obsidian usa para organizar, filtrar y buscar.

**Glitch**
Falla momentánea y sin causa clara en el comportamiento de un programa — algo se ve o se comporta raro por un instante, pero no refleja un problema real en la lógica del sistema. No es reproducible de forma consistente (a veces pasa, a veces no) y generalmente se resuelve reintentando la acción. Distinto de un bug: un bug es un error consistente en el código (dado el mismo input, siempre falla igual, y hay que corregir el código); un glitch es más errático, a menudo un problema de timing o de cómo una interfaz web maneja el foco de un campo. Ej: al publicar un workflow en n8n (2026-09-13), el campo "Version name" no se vació antes de escribir el texto nuevo y quedó todo pegado — no fue un error del workflow, fue el campo de texto comportándose mal por un instante.

**Glob**
Patrón para nombrar archivos usando comodines en vez del nombre exacto — el más común es `*`, que matchea cualquier texto. Se usa mucho en `.gitignore`: por ejemplo `movimientos_manuales*.csv` cubre tanto `movimientos_manuales.csv` como `movimientos_manuales broker.csv`, sin tener que listar cada variante de nombre a mano.

**Grep**
Herramienta de búsqueda de texto dentro de archivos: busca una palabra o patrón específico y dice en qué archivo y línea aparece. Es la forma rápida de "encontrar dónde está esto" en todo el código o los datos, sin abrir archivo por archivo a mano — por ejemplo, cuando Code buscó un ticker en 3 años de exports de un broker.

**Hack**
Solución práctica y rápida a un problema — no necesariamente la más elegante, pero que funciona bien y ahorra tiempo. Puede ser un atajo, un truco de herramienta, o una forma no obvia de resolver algo tedioso. No tiene la connotación negativa de "hackear" un sistema sin permiso; acá es simplemente "truco útil". Ej: usar `@`-mention en vez de pegar un archivo completo, o `git stash` para guardar cambios a medio hacer sin commitearlos.

**Hash de commit**
Código único que Git le asigna a cada commit (se muestra recortado a 7 caracteres, ej. `8d5e671`), plural "hashes". Es el resultado de convertir toda la información de ese commit en un código corto e irrepetible — como una huella digital: dos commits distintos casi nunca comparten hash, y el mismo commit siempre da el mismo hash. Sirve para referenciar ese punto exacto de la historia ("volver al estado de tal commit") en vez de buscarlo por fecha o mensaje.

**Label**
Etiqueta de texto que sirve para que una persona identifique algo, pero que no tiene ningún efecto sobre el funcionamiento real de lo que etiqueta — puramente descriptivo/organizativo, nunca funcional. Cambiar un label nunca debería cambiar qué hace el sistema. Ej: renombrar los nodos de un workflow de n8n ("Schedule Trigger - Precios 18:05" en vez de "Schedule Trigger3") es cambiar un label — n8n sigue ejecutando exactamente la misma lógica (mismo cron, misma URL) sin importar cómo se llame el nodo.

**Wikilink**
Forma de enlazar una nota con otra dentro de Obsidian usando doble corchete: `[[nombre-de-la-nota]]`. Al hacer clic (o tocar Ctrl y clic) te lleva directo a esa nota. Es lo que arma la red de conexiones entre las notas. La documentación de PWOS no usa wikilinks sino links Markdown estándar (`[texto](ruta.md)`), porque además de Obsidian funcionan en GitHub y VS Code — ver la [guía de Obsidian](guias/como-armar-obsidian.md).

**Workspace**
La carpeta que se abre en VS Code como proyecto activo. Claude Code solo ve y opera sobre archivos dentro de esa carpeta.

---

## Links relacionados

- [Arquitectura general](arquitectura/arquitectura-general.md)
- [Modelo de dominio](arquitectura/modelo-de-dominio.md)
- [Schema de la base de datos](arquitectura/schema-base-datos.md)
- [Decisiones de arquitectura](decisiones/README.md)
