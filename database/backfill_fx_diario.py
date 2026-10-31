"""
PWOS -- Backfill de serie DIARIA CONTINUA de USD_MEP/USD_CCL (no solo
fechas puntuales como los backfills anteriores de FX -- ver
backfill_fx_historico_ganancia_2026-08-20.py y
backfill_fx_historico_rendimiento_2026-08-21.py, que solo cubrian
fechas concretas de flujos/anclas ya conocidas).

Motivo (Tanda 2, item 5 del barrido de pendientes 2026-08-31): el
relevamiento del 2026-08-27 (ver ESTADO_PWOS.md, entrada "Backfill de
precio historico de acciones/CEDEARs...") encontro que de los 1.093 dias
habiles sin ancla de Rendimiento, 311 (28%) fallaban UNICAMENTE por
falta de MEP/CCL de esa fecha exacta -- no por falta de precio de ningun
activo. `rendimiento.py::_efectivo_a_fecha`/`patrimonio_a_fecha` exigen
match EXACTO de fecha para USD_MEP (todo dia, tenga o no activos en ARS
-- ver docstring de _efectivo_a_fecha) y USD_CCL (solo si hay algun
activo tipo CCL vigente ese dia), sin aproximar. Traer la serie DIARIA
completa (no una lista curada de fechas) es lo unico que puede cerrar
ese hueco de raiz, incluyendo dias futuros aun no identificados como
relevantes.

Misma fuente que los backfills anteriores: api.argentinadatos.com,
campo `venta` (misma convencion que dry_run_precios.py y los backfills
de FX previos). Se trae la serie COMPLETA de cada endpoint (no hay forma
de pedir un rango a esta API) e insertan solo las fechas que faltan via
INSERT OR IGNORE -- backup + integrity_check previos, todo en una
transaccion, foreign_key_check antes de commitear.

Uso:
    python backfill_fx_diario_completo_2026-08-31.py
"""
import shutil
import sqlite3
from pathlib import Path

import requests
import configuracion

DB_PATH = configuracion.ruta_db()

MEP_HIST_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"
CCL_HIST_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/contadoconliqui"


def descargar_serie(url: str) -> dict:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return {d["fecha"]: d["venta"] for d in r.json()}


def main():
    con = sqlite3.connect(DB_PATH)

    print("Descargando series historicas completas de ArgentinaDatos...")
    mep_hist = descargar_serie(MEP_HIST_URL)
    ccl_hist = descargar_serie(CCL_HIST_URL)
    print(f"  MEP: {len(mep_hist)} fechas ({min(mep_hist)} a {max(mep_hist)})")
    print(f"  CCL: {len(ccl_hist)} fechas ({min(ccl_hist)} a {max(ccl_hist)})")

    ya_mep = {
        r[0] for r in con.execute(
            "SELECT p.fecha FROM precios p JOIN activos a ON a.id=p.activo_id WHERE a.ticker='USD_MEP'"
        ).fetchall()
    }
    ya_ccl = {
        r[0] for r in con.execute(
            "SELECT p.fecha FROM precios p JOIN activos a ON a.id=p.activo_id WHERE a.ticker='USD_CCL'"
        ).fetchall()
    }
    faltan_mep = {f: v for f, v in mep_hist.items() if f not in ya_mep}
    faltan_ccl = {f: v for f, v in ccl_hist.items() if f not in ya_ccl}
    print(f"\nFaltan cargar: {len(faltan_mep)} MEP, {len(faltan_ccl)} CCL")

    if not faltan_mep and not faltan_ccl:
        print("Nada para insertar, todo ya estaba cubierto.")
        con.close()
        return

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "pwos_backup_pre-fx-diario-completo_2026-08-31.db"
    if backup_path.exists():
        raise RuntimeError(f"{backup_path} ya existe - no lo piso, revisar a mano")
    shutil.copy2(DB_PATH, backup_path)
    con_chk = sqlite3.connect(backup_path)
    ok = con_chk.execute("PRAGMA integrity_check;").fetchone()[0]
    con_chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"\nBackup OK: {backup_path} (integrity_check=ok)")

    activo_mep = con.execute("SELECT id FROM activos WHERE ticker='USD_MEP'").fetchone()[0]
    activo_ccl = con.execute("SELECT id FROM activos WHERE ticker='USD_CCL'").fetchone()[0]

    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("BEGIN;")
    insertados = 0
    try:
        for fecha, venta in faltan_mep.items():
            cur = con.execute(
                "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, ?, ?, 'ARS', 'argentinadatos_historico_diario');",
                (activo_mep, fecha, venta),
            )
            insertados += cur.rowcount
        for fecha, venta in faltan_ccl.items():
            cur = con.execute(
                "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, ?, ?, 'ARS', 'argentinadatos_historico_diario');",
                (activo_ccl, fecha, venta),
            )
            insertados += cur.rowcount

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"\nCOMMIT OK. Filas insertadas: {insertados}")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
