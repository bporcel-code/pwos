---
título: Cómo leer la documentación en Obsidian
tipo: guia
estado: activo
creado: 2026-07-09
actualizado: 2026-10-17
tags: [obsidian, setup, guia]
---

# Cómo leer la documentación en Obsidian

> La carpeta `docs/` es Markdown puro: se lee en GitHub, en VS Code o en Obsidian. Esta guía explica cómo abrirla en Obsidian para navegarla como una red de notas.

---

## 1. Abrir la carpeta como vault

1. Instalar Obsidian desde [obsidian.md](https://obsidian.md).
2. En la pantalla de bienvenida, elegir **"Abrir carpeta como vault"**.
3. Seleccionar la carpeta `docs/` del repositorio, o la raíz del repo si se quiere ver todo junto.

Un vault es solo una carpeta con archivos `.md`. Obsidian no usa una base de datos propia, así que los mismos archivos se siguen editando con VS Code y versionando con Git.

---

## 2. Estructura

```
docs/
├── arquitectura/
│   ├── arquitectura-general.md   ← cómo se conectan las piezas
│   ├── modelo-de-dominio.md      ← el vocabulario del sistema
│   └── schema-base-datos.md      ← tablas y relaciones (generado por script)
├── decisiones/
│   ├── README.md                 ← índice de ADRs
│   └── adr/                      ← un archivo por decisión
├── guias/
│   └── como-armar-obsidian.md    ← esta guía
└── glosario.md                   ← términos técnicos
```

Cada documento responde una sola pregunta:

| Pregunta | Documento |
|---|---|
| ¿Cómo está construido el sistema? | [Arquitectura general](../arquitectura/arquitectura-general.md) |
| ¿Qué significa cada concepto? | [Modelo de dominio](../arquitectura/modelo-de-dominio.md) |
| ¿Qué tablas hay y cómo se relacionan? | [Schema](../arquitectura/schema-base-datos.md) |
| ¿Por qué se decidió así? | [Decisiones](../decisiones/README.md) |
| ¿Qué quiere decir esta palabra? | [Glosario](../glosario.md) |

---

## 3. Links entre notas

La documentación usa **links Markdown estándar**, con ruta relativa:

```markdown
[Modelo de dominio](../arquitectura/modelo-de-dominio.md)
```

Se usan en lugar de los wikilinks de Obsidian (`[[nota]]`) porque funcionan en todos lados: GitHub, VS Code y Obsidian.

Para que Obsidian también cree links en este formato, conviene ajustar dos opciones en **Configuración → Archivos y enlaces**:

- **Usar [[Wikilinks]]:** desactivado.
- **Formato de nuevo enlace:** "Ruta relativa al archivo".

---

## 4. Diagramas

Los bloques ` ```mermaid ` (por ejemplo, el mapa de componentes de la arquitectura) Obsidian los dibuja solo, sin instalar nada.

El **Graph View** (Ctrl+G) muestra las notas como nodos y los links como líneas. Sirve para ver de un vistazo qué documentos se referencian entre sí.

---

## 5. Plugins útiles (opcionales)

- **Dataview:** permite consultar las notas como una base de datos, por ejemplo para listar los ADRs por estado.
- **Git:** permite hacer commit y push desde Obsidian. Si ya se usa VS Code y la terminal, no hace falta.

---

## 6. Cómo agregar una decisión de arquitectura

1. Crear un archivo nuevo en `decisiones/adr/`, con el siguiente número libre: `ADR-NNN-titulo-corto.md`.
2. Usar la plantilla de las reglas del [índice](../decisiones/README.md): Estado · Fecha · Relacionados / Excepciones · Contexto · Decisión · Alternativas descartadas · Consecuencias.
3. Agregar la fila correspondiente a la tabla del índice.
4. Si la decisión reemplaza a otra, el ADR viejo **no se reescribe**: solo cambia su estado a *Superada por* o *Modificada por*.

---

## Links relacionados

- [Arquitectura general](../arquitectura/arquitectura-general.md)
- [Modelo de dominio](../arquitectura/modelo-de-dominio.md)
- [Decisiones de arquitectura](../decisiones/README.md)
- [Glosario](../glosario.md)
