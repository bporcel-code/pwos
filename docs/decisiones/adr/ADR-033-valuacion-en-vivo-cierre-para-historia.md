# ADR-033 — Valuación en vivo durante la rueda, cierre diario para la historia

**Estado:** Aceptada
**Fecha:** 2026-10-27
**Relacionados / Excepciones:** Relacionados: ADR-001, ADR-012, ADR-015, ADR-018, ADR-023.

## Contexto

La tabla `precios` guarda una cotización por activo y por día. Todo lo que muestra "cuánto vale hoy" (patrimonio, efectivo, composición, ganancia no realizada, TIR de hoy) leía la fila más reciente de esa tabla.

Las cotizaciones se escribían una vez por día, al cierre (job programado de la tarde), más un proceso que completa los días perdidos con el cierre de una fuente histórica. Eso tenía dos efectos:

- **Durante la rueda se valuaba con el cierre del día anterior,** tanto el tipo de cambio como los precios. El número del dashboard no reflejaba lo que estaba pasando en el mercado.
- **Un refresco a mano en plena rueda dejaba un precio intradía como cierre del día.** La tabla usa "insertar si no existe": el job de la tarde ya no lo reemplazaba.

Las dos cosas que se usan con esos datos piden cosas distintas: la valuación del momento necesita el último dato, y la historia (rendimiento, cierres mensuales, snapshots) necesita un cierre por día, comparable entre días.

## Decisión

- **Valuación en vivo durante la rueda** (días hábiles, de 11:00 a 17:00, hora de Argentina): el tipo de cambio (MEP, CCL) **y** los precios de lo que está en cartera se piden en el momento a las mismas fuentes públicas que usa el cierre, **sin guardarlos**. Una caché de pocos minutos evita saturar las fuentes.
- **Un solo punto de decisión:** el módulo de valorización (ADR-015) expone el precio y el tipo de cambio "para valuar hoy". Por defecto devuelven el último cierre; solo la API habilita el modo en vivo.
- **Si la fuente falla, el cierre, nunca un error ni un cero:**
  - Si falla el tipo de cambio, **toda** la valuación vuelve al último cierre.
  - Si falla una fuente de precios, un activo no operó en el día o su moneda no coincide con la del cierre guardado, solo ese activo usa el cierre, y la pantalla lo lista.
  - Fuera de la rueda se usa el último cierre. Los feriados se detectan por la fuente: si el tipo de cambio no tiene dato del día, no hubo rueda.
- **`precios` guarda solo cierres:** el job de la tarde y el proceso que completa los días perdidos. El importador no guarda nada un día hábil antes de las 17:00, aunque lo corra una persona a mano. La fecha que se guarda es la de Argentina, no la del servidor.
- **La historia sigue con el cierre diario:** rendimiento, cierres mensuales (ADR-023), el snapshot diario de bonos y todo lo que escribe en la base (estimados y conciliación de cobros) se calculan siempre con el último cierre, aunque la rueda esté abierta.
- **El snapshot diario de cartera es historia:** se calcula con el cierre y su disparador (ADR-018) arranca **después** del job de cierre, cada hora de 19 a 23, quedándose con el primero que sale bien. Antes arrancaba a las 15:00 y quedaba con el cierre del día anterior, o con un intradía si la valuación hubiera sido en vivo.
- **La pantalla muestra de cuándo es el dato:** "cotizaciones de las HH:MM" o "cierre del DD/MM", con un aviso cuando alguna cotización cayó al cierre por una falla de la fuente.

## Alternativas descartadas

- **Solo el tipo de cambio en vivo y los precios del último cierre:** mezcla el dólar de hoy con el precio de ayer. Todo lo que cotiza en pesos (acciones locales, CEDEARs, bonos en pesos) se divide por un tipo de cambio que no corresponde a su precio, y la valuación en dólares sale distorsionada justo en los días en que el dólar se mueve.
- **Guardar los precios intradía en `precios`** (una fila por consulta, o pisar la del día): la tabla deja de ser un cierre por día, la historia pierde comparabilidad y un día con una sola consulta a la mañana queda con un "cierre" que no lo es.
- **Seguir valuando con el último cierre todo el día:** simple, pero el número que se mira durante la rueda llega con un día de atraso.

## Consecuencias

- Abrir el dashboard en la rueda hace hasta 7 llamadas a las fuentes públicas cada vez que vence la caché. Fuera de la rueda no hace ninguna.
- El número del dashboard cambia durante el día y, al cerrar, puede diferir un poco del cierre que se guarda después (último operado contra cierre de la fuente). La historia no se ve afectada.
- Todo código nuevo que valúe "hoy" tiene que pasar por el módulo de valorización. Si lee `precios` directo, valúa con el cierre. Todo código nuevo que escriba historia tiene que correr en el modo de solo cierre.
- La demora real de la fuente de precios en vivo no está publicada: se mide comparando contra la app de un broker durante la rueda.
