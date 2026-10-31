"""
alta_activos_bonar_global_cer_2026-09-18.py -- PWOS

Alta en el catalogo `activos` de los tickers de Bonares/Globales/CER
que el usuario NUNCA opero (por eso no estaban en `activos` -- todo el
proyecto solo daba de alta un ticker cuando aparecia en un movimiento
real). Paso previo obligatorio antes de poder backfillear su precio
historico: `precios.activo_id` es FK a `activos.id`.

Universo: clasificar_bonos.clasificar() sobre TODO `mercado_renta_fija_
rava` (~890 especies), filtrado a categoria in ('bonar','global','cer')
-- 69 tickers en total, de los cuales 15 ya estaban catalogados (los
que el usuario opero alguna vez). Sin posicion ni movimiento real para
ninguno de estos 54 -- son de referencia pura, para poder comparar
ratios de precio contra el universo completo de estas 3 categorias
(2026-09-18), no para trackear cartera.

`tipo`/`moneda_nominal`: bonar/global -> 'bono_soberano'/'USD' (deuda
hard-dollar, mismo criterio que AL30/GD29 ya catalogados); cer ->
'bono_cer'/'ARS' (deuda en pesos ajustada por CER, mismo criterio que
TX26/TX31 ya catalogados) -- verificado que NINGUNA especie D/C de
bonar/global tiene moneda_nominal distinta de la base (AL30D es
'USD' igual que AL30, confirmado contra la base real antes de escribir
este script).

Uso (parado en database/):
    python alta_activos_bonar_global_cer_2026-09-18.py
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from clasificar_bonos import clasificar
import configuracion

DB_PATH = configuracion.ruta_db()

CATEGORIA_A_TIPO_MONEDA = {
    "bonar": ("bono_soberano", "USD"),
    "global": ("bono_soberano", "USD"),
    "cer": ("bono_cer", "ARS"),
}


def hacer_backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos.db.backup_alta_activos_bonar_global_cer_{datetime.now():%Y-%m-%d_%H%M%S}"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")
    return backup_path


def main():
    con = sqlite3.connect(DB_PATH, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")

    filas = con.execute("SELECT ticker, tipo_instrumento, legislacion FROM mercado_renta_fija_rava;").fetchall()
    universo = {}
    for ticker, tipo_instrumento, legislacion in filas:
        cat = clasificar(ticker, tipo_instrumento, legislacion)
        if cat in CATEGORIA_A_TIPO_MONEDA:
            universo[ticker] = cat

    ya_existen = {r[0] for r in con.execute("SELECT ticker FROM activos;").fetchall()}
    faltan = sorted(t for t in universo if t not in ya_existen)

    print(f"Universo Bonares/Globales/CER: {len(universo)} tickers. Ya catalogados: {len(universo) - len(faltan)}. A crear: {len(faltan)}.")
    if not faltan:
        print("Nada para insertar.")
        con.close()
        return

    hacer_backup(DB_PATH)

    con.execute("BEGIN;")
    try:
        insertados = 0
        for ticker in faltan:
            tipo, moneda = CATEGORIA_A_TIPO_MONEDA[universo[ticker]]
            con.execute(
                "INSERT INTO activos (ticker, tipo, moneda_nominal) VALUES (?, ?, ?);",
                (ticker, tipo, moneda),
            )
            insertados += 1
            print(f"  + {ticker:<8} {tipo:<14} {moneda}")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"\nCOMMIT OK. Insertados: {insertados}.")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
