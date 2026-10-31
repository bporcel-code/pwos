"""
PWOS -- backfill UNICO de `macro_historico` con la serie interanual
COMPLETA de cada indicador de Epica 16 (2026-09-15:
"donde la fuente tenga historial, backfill completo al implementar").
Mismo patron que backfill_macro_historico.py (Epica 12 Parte 4): esto
se corre UNA VEZ para llenar el pasado; de ahi en adelante
`cargar_indicadores_economia_real.py` (cadencia mensual) sigue
alimentando `macro_historico` en cada corrida (via `append_macro_
historico`, agregado el mismo dia que este backfill -- ver docstring
de ese modulo), asi el grafico historico del panel "Indicadores Macro"
no se queda fijo en la fecha de este backfill.

`clave` usa los MISMOS nombres que `tesis_umbrales.indicador` (mismo
criterio que toda la tabla `macro_historico`).

Rango real de cada fuente (verificado 2026-09-15, ver docstring de
cargar_indicadores_economia_real.py para el detalle de por que difiere
del brief original):
  - patentamientos_interanual_pct: 2001-01 en adelante (308 puntos)
  - construccion_interanual_pct:   2013-01 en adelante (163 puntos)
  - empleo_privado_interanual_pct: 2010-01 en adelante (198 puntos)
  - mora_hogares_pct:              2010-01 en adelante (198 puntos)
  - industria_interanual_pct:      2017-01 en adelante (115 puntos)

Uso:
    python backfill_indicadores_economia_real.py
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from cargar_indicadores_economia_real import (
    obtener_construccion_historico,
    obtener_empleo_privado_historico,
    obtener_industria_historico,
    obtener_mora_hogares_historico,
    obtener_patentamientos_historico,
)
import configuracion

DB_PATH = configuracion.ruta_db()


def hacer_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-backfill-economia-real_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")
    return backup_path


def backfill(db_path: Path = DB_PATH) -> None:
    hacer_backup(db_path)

    fuentes = [
        ("patentamientos_interanual_pct", obtener_patentamientos_historico),
        ("construccion_interanual_pct", obtener_construccion_historico),
        ("empleo_privado_interanual_pct", obtener_empleo_privado_historico),
        ("mora_hogares_pct", obtener_mora_hogares_historico),
        ("industria_interanual_pct", obtener_industria_historico),
    ]

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        con.execute("BEGIN;")
        for clave, fn in fuentes:
            print(f"=== {clave} ===")
            puntos = fn()
            insertados = 0
            for p in puntos:
                cur = con.execute(
                    "INSERT OR IGNORE INTO macro_historico (clave, fecha, valor) VALUES (?, ?, ?);",
                    (clave, p["fecha"], round(p["valor"], 4)),
                )
                insertados += cur.rowcount
            print(f"  {len(puntos)} puntos de la fuente, {insertados} insertados nuevos "
                  f"(desde {puntos[0]['fecha'] if puntos else '-'} hasta {puntos[-1]['fecha'] if puntos else '-'})")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nBackfill OK.")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nBACKFILL ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    backfill()
