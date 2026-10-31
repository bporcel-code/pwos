---
título: Hoja de ruta
tipo: roadmap
estado: activo
creado: 2026-10-27
actualizado: 2026-10-27
tags: [pwos, roadmap]
---

# Hoja de ruta

> Qué está construido, qué está en curso y qué viene. El detalle de cada decisión está en las [decisiones de arquitectura](decisiones/README.md).

## Construido

| Área | Qué incluye |
|---|---|
| **Fundamentos** | Modelo basado en movimientos; base SQLite con migraciones versionadas; valuación centralizada en dólar MEP |
| **Ingesta de brokers** | Importadores para 5 brokers argentinos, con dry run, carga incremental y deduplicación por identificador |
| **Ingesta de bancos y billeteras** | Importadores para bancos y billeteras virtuales (Excel y PDF), plazos fijos y cuentas remuneradas |
| **Contenedores especiales** | Contenedores sin export (ajuste manual) y contenedores compartidos (porcentaje de propiedad aplicado al valuar) |
| **Precios y mercado** | Feed diario de acciones, CEDEARs, bonos y tipos de cambio; cronogramas de pago de bonos; universo completo de renta fija |
| **Automatización** | n8n con tareas programadas, puesta al día automática de huecos y alertas por mail |
| **Infraestructura y seguridad** | Docker, acceso solo por Tailscale, credenciales en el gestor del sistema operativo, cifrado en reposo |
| **Dashboard** | Patrimonio, composición, efectivo vs. invertido, ganancia realizada y no realizada, rendimiento mensual |
| **Análisis de bonos** | TIR (también real para CER), duration, BPV, carry, breakeven, curva por categoría, comparador y ratio histórico |
| **Análisis de acciones** | Concentración, cubetas, ficha de posición con semáforos, screener, watchlist, frontera eficiente, CEDEAR en pesos vs. dólares |
| **Macro** | Más de 60 indicadores argentinos y globales con historial, umbrales de velocidad y panel por ciclo económico |
| **Tesis de inversión** | Bitácoras por área con umbrales medibles que se chequean solos |
| **Flujo de cobros** | Cupones, dividendos y alquileres: estimado vs. cobrado, mes a mes |
| **Real Estate** | Propiedades, valuaciones, alquileres, gastos, deuda, TIR y VPN, co-propiedad |
| **Documentación** | ADRs, arquitectura, modelo de dominio, schema generado automáticamente, glosario |

## En curso

- **Versión pública de demostración:** repositorio de vitrina generado con lista blanca y una base de datos ficticia para poder levantar el dashboard de punta a punta.

## Próximo

| Tema | Por qué |
|---|---|
| **Componente impositivo** | Estimaciones y alertas de Bienes Personales y Ganancias (orientativo, no reemplaza a un contador) |
| **Asesor con IA** | Que el sistema interprete los cambios del mes, los riesgos y el estado de cada tesis |

## A futuro (si el proyecto escala)

Autenticación, multiusuario, PostgreSQL, frontend en React y despliegue en la nube. No se incorporan antes de que haga falta: primero se valida la necesidad, después se escala.
