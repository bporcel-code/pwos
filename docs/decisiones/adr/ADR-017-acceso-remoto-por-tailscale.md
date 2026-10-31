# ADR-017 — Acceso remoto solo por red privada (Tailscale), sin exposición pública

**Estado:** Aceptada
**Fecha:** 2026-07-29
**Relacionados / Excepciones:** Relacionados: ADR-016, ADR-018, ADR-022.

## Contexto

El dashboard (ADR-016) corre en la PC del usuario y tiene que poder abrirse desde el celular. Exponerlo a internet obligaría a construir autenticación, TLS y endurecimiento antes de la primera versión, para un sistema de un solo usuario con datos patrimoniales.

## Decisión

- El acceso remoto es **solo a través de una red privada Tailscale**. Nada se expone a internet público.
- El backend escucha en `0.0.0.0` (todas las interfaces, incluida la de Tailscale), no solo en `localhost`. Si no, el celular no puede llegar aunque Tailscale esté bien configurado.
- La regla de Firewall de Windows para el puerto del dashboard está acotada al **perfil de red Privada**, donde cae la interfaz de Tailscale, y no al perfil Público (Wi-Fi).
- **Sin login en la aplicación:** la protección es perimetral, por la red privada.

## Alternativas descartadas

- **Exponer el servicio a internet (puerto abierto o túnel público) con autenticación propia:** más superficie de ataque y más trabajo, sin necesidad para un único usuario.

## Consecuencias

- Cualquier dispositivo dentro de la red Tailscale tiene acceso total al dashboard. La seguridad depende de mantener esa red acotada a dispositivos propios.
- Si algún día el servicio se expone de otra forma, la autenticación deja de ser opcional y hace falta un ADR nuevo.
- Cada servicio con puerto propio (por ejemplo, n8n) necesita su propia regla de firewall con el mismo criterio.
- La dirección del equipo en la red privada es un dato de configuración local y no se documenta en `docs/`.
