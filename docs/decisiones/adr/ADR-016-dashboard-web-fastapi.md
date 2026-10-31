# ADR-016 — Dashboard web (FastAPI + HTML/JS) como interfaz principal

**Estado:** Aceptada
**Fecha:** 2026-07-29
**Relacionados / Excepciones:** Relacionados: ADR-005, ADR-015, ADR-017, ADR-019.

## Contexto

La única interfaz era un tablero de texto en la terminal. Hacía falta ver el patrimonio desde el celular y armar paneles de análisis. El bot de Telegram planeado en ADR-005 nunca se construyó.

## Decisión

- **Backend:** FastAPI (`database/api.py`) con endpoints JSON, en su mayoría de solo lectura.
- **Frontend:** HTML + CSS + JavaScript plano en `database/static/`, sin build step.
- El backend reusa el módulo de valorización (ADR-015) y no reimplementa ningún cálculo.

Un motivo explícito del usuario para elegir este stack: aprender el patrón API + cliente web, que es el más reconocido en el mercado laboral.

## Alternativas descartadas

- **Streamlit:** resuelve el mismo problema más rápido, pero no enseña el patrón API + cliente y acopla la interfaz al cálculo.

## Consecuencias

- La API es la frontera única entre los datos y cualquier interfaz. Otras herramientas (n8n, ADR-018) también la consumen.
- Los endpoints de escritura son excepciones acotadas (por ejemplo, cargar una tesis de inversión o disparar un refresco de precios).
- La librería de gráficos externa de la primera versión se retiró después (2026-08-08), a favor de gráficos propios.
- El acceso remoto se resuelve por red privada (ADR-017) y el servicio corre en un contenedor (ADR-019).
