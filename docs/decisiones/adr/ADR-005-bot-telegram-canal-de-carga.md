# ADR-005 — Interfaz de carga: bot de Telegram como canal principal móvil

**Estado:** Superada por ADR-016 y ADR-012
**Fecha:** anterior a 2026-07-04 (primer commit del repo)
**Relacionados / Excepciones:** Relacionados: ADR-003, ADR-006, ADR-021.

## Contexto

El objetivo de fondo del usuario no era el bot en sí. Era tener un enlace tipo API con cada contenedor (banco, broker, billetera) para ver cada movimiento en PWOS apenas ocurre y unificar toda la información en un solo lugar.

Mientras esa vía no existiera, hacía falta un canal de carga manual. Con cientos de movimientos por mes, cargar a mano en una base de datos o en una interfaz web tiene demasiada fricción para sostenerse en el tiempo. Las apps de finanzas personales se abandonan justamente porque la carga es tediosa.

## Decisión

El canal principal de carga rápida desde el celular sería un **bot de Telegram**. El usuario envía un mensaje como `"nafta 5000"` y el bot lo interpreta y lo registra.

Por qué Telegram:

- Ya está instalado; no hay que instalar nada nuevo.
- La interfaz de chat es la de menor fricción posible.
- Python tiene una librería madura para bots (`python-telegram-bot`).
- Los botones inline permiten confirmar categorías sin escribir.

## Alternativas descartadas

- **App móvil nativa:** costo de desarrollo desproporcionado para un MVP.
- **PWA web:** requiere abrir el navegador e ir a una URL; más fricción que Telegram.
- **Formulario en el editor de código:** solo viable en la computadora; no resuelve la carga en el momento.

## Consecuencias

- El bot nunca se construyó.
- La vía API también se descartó: los bancos y billeteras no ofrecen API de movimientos, y la automatización contra el broker que sí la tiene se descartó por seguridad (ADR-021).
- Hoy la carga la cubren los importadores de extractos con actualización incremental (ADR-006, ADR-012), y la interfaz es el dashboard web (ADR-016).
- El objetivo de fondo (capturar movimientos casi en tiempo real) sigue abierto. Si se implementa por otra vía, por ejemplo leyendo las notificaciones por email que envían los contenedores, tendrá su propio ADR.
