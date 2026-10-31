"""
generar_doc_schema.py

Genera docs/arquitectura/schema-base-datos.md leyendo la ESTRUCTURA de la base:
tablas, columnas, claves primarias, relaciones (foreign keys) y vistas.

Nunca lee filas (datos): solo metadatos. Por eso el documento resultante es
publicable (respeta la regla de oro de docs/).

Uso (desde la raíz del repo):
    python database/generar_doc_schema.py

Ruta de la base: variable de entorno PWOS_DB_PATH; si no está definida, la de
la configuración (configuracion.ruta_db()). La conexión es de SOLO LECTURA.

Correrlo después de cada migración de schema y commitear el .md resultante.
"""

import os
import sqlite3
from pathlib import Path

import configuracion

RUTA_DB = os.environ.get("PWOS_DB_PATH") or str(configuracion.ruta_db())
RAIZ_REPO = Path(__file__).resolve().parent.parent
SALIDA = RAIZ_REPO / "docs" / "arquitectura" / "schema-base-datos.md"


def leer_estructura(con):
    """Devuelve {tabla: {"columnas": [...], "fks": [...]}} y la lista de vistas."""
    tablas = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    vistas = [
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name"
        )
    ]

    estructura = {}
    for tabla in tablas:
        # PRAGMA no acepta parámetros (?), por eso el nombre va en el texto.
        # Es seguro: el nombre sale de la propia base, no de un usuario.
        columnas = con.execute(f'PRAGMA table_info("{tabla}")').fetchall()
        # columnas: (cid, name, type, notnull, dflt_value, pk)
        fks = con.execute(f'PRAGMA foreign_key_list("{tabla}")').fetchall()
        # fks: (id, seq, table, from, to, on_update, on_delete, match)
        estructura[tabla] = {"columnas": columnas, "fks": fks}
    return estructura, vistas


def celda(valor):
    """Texto seguro para una celda de tabla Markdown."""
    if valor is None:
        return ""
    return str(valor).replace("|", "\\|").replace("\n", " ")


def generar_markdown(estructura, vistas):
    total_fks = sum(len(t["fks"]) for t in estructura.values())
    lineas = []

    # Encabezado
    lineas += [
        "---",
        "título: Schema de Base de Datos",
        "tipo: arquitectura",
        "generado: automáticamente por database/generar_doc_schema.py",
        "---",
        "",
        "# Schema de base de datos",
        "",
        "> ⚠️ **Archivo generado automáticamente** por `database/generar_doc_schema.py`.",
        "> No editar a mano: los cambios se pierden la próxima vez que se genere.",
        "> Para actualizarlo, correr el script después de cada migración y commitear el resultado.",
        "",
        f"**Resumen:** {len(estructura)} tablas · {len(vistas)} vistas · {total_fks} relaciones (foreign keys).",
        "",
        "El porqué de las decisiones de diseño está en [las decisiones de arquitectura](../decisiones/README.md).",
        "",
    ]

    # Diagrama de relaciones (Mermaid, se dibuja solo en GitHub y en Obsidian)
    lineas += ["## Diagrama de relaciones", ""]
    if total_fks:
        lineas += [
            "Solo muestra las tablas que tienen relaciones declaradas con otras.",
            "",
            "```mermaid",
            "erDiagram",
        ]
        for tabla, info in estructura.items():
            for fk in info["fks"]:
                destino, columna = fk[2], fk[3]
                lineas.append(f'    {tabla} }}o--|| {destino} : "{columna}"')
        lineas += ["```", ""]
    else:
        lineas += ["La base no declara foreign keys.", ""]

    # Índice
    lineas += ["## Índice de tablas", ""]
    lineas += [f"- [{t}](#{t})" for t in estructura]
    lineas.append("")

    # Detalle de cada tabla
    lineas += ["## Tablas", ""]
    for tabla, info in estructura.items():
        lineas += [
            f"### {tabla}",
            "",
            "| Columna | Tipo | Obligatoria | Default | Clave primaria |",
            "|---|---|---|---|---|",
        ]
        for cid, nombre, tipo, notnull, default, pk in info["columnas"]:
            lineas.append(
                f"| `{celda(nombre)}` | {celda(tipo) or '—'} | "
                f"{'sí' if notnull else ''} | {celda(default)} | "
                f"{'✓' if pk else ''} |"
            )
        if info["fks"]:
            lineas += ["", "**Relaciones:**", ""]
            for fk in info["fks"]:
                destino, origen, col_destino = fk[2], fk[3], fk[4]
                col_destino = col_destino or "(clave primaria)"
                lineas.append(f"- `{origen}` → `{destino}.{col_destino}`")
        lineas.append("")

    # Vistas
    lineas += ["## Vistas", ""]
    if vistas:
        lineas.append("Consultas guardadas en la base (no guardan datos propios).")
        lineas.append("")
        lineas += [f"- `{v}`" for v in vistas]
    else:
        lineas.append("La base no tiene vistas.")
    lineas.append("")

    return "\n".join(lineas)


def main():
    if not Path(RUTA_DB).exists():
        raise SystemExit(
            f"No encuentro la base en {RUTA_DB}. "
            "¿Está montado el volumen VeraCrypt? (o definí PWOS_DB_PATH)"
        )

    con = sqlite3.connect(f"file:{RUTA_DB}?mode=ro", uri=True)
    try:
        estructura, vistas = leer_estructura(con)
    finally:
        con.close()

    texto = generar_markdown(estructura, vistas)
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(texto, encoding="utf-8")

    total_fks = sum(len(t["fks"]) for t in estructura.values())
    print(f"OK: {len(estructura)} tablas, {len(vistas)} vistas, {total_fks} relaciones.")
    print(f"Escrito en: {SALIDA}")


if __name__ == "__main__":
    main()
