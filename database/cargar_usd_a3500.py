"""
PWOS -- Carga USD_A3500 (tipo de cambio mayorista de referencia del
BCRA, Comunicacion "A" 3500) como activo tipo 'fx' + su serie en `precios`.

Por que un ticker nuevo, separado de USD_MEP/USD_CCL: los bonos
dollar-linked se liquidan en pesos aplicando la formula
oficial de la Comunicacion A3500 del BCRA, NO contra el MEP -- usar MEP
ahi metería la brecha MEP-A3500 en un instrumento que no tiene relacion
con esa brecha (decision del usuario, 2026-08-12). Lo usan
vista_flujo_fondos_bonos y flujo_estimado.py (cobros de bonos dollar-linked en USD).

Fuente (2026-10-14): API de estadisticas v4 del BCRA, variable 5 ("Tipo
de cambio mayorista de referencia" = Com. A 3500), diaria desde
2002-03-04 -- la serie oficial, con historia. La version anterior tomaba
`venta` de dolarapi.com/v1/dolares/mayorista y solo el dia de hoy: se
corrio una sola vez (2026-08-12) y nunca quedo programada, asi que
USD_A3500 tenia UNA fila. Ademas dolarapi no es exactamente el A3500
(14/10: 1.513 vs 1.514,5655 del BCRA).

Re-ejecutable: UPSERT en `precios` por (activo_id, fecha), `fuente='bcra'`;
una fecha ya cargada con el mismo valor no se toca.

Diario: importador_precios.py llama a `actualizar()` con una ventana de
DIAS_VENTANA dias -- si un dia falla, el siguiente lo completa.

Uso (parado en database/):
    python cargar_usd_a3500.py --backfill              # serie completa desde 2002 (V:)
    python cargar_usd_a3500.py                         # ultimos DIAS_VENTANA dias
    python cargar_usd_a3500.py --db COPIA.db --backfill
"""
import argparse
import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from cargar_indicadores_macro import bcra_serie
import configuracion

DB_PATH = configuracion.ruta_db()

BCRA_ID_A3500 = 5  # "Tipo de cambio mayorista de referencia" (Com. A 3500)
DIAS_VENTANA = 15


def _activo_a3500(con) -> int:
    row = con.execute("SELECT id FROM activos WHERE ticker = 'USD_A3500';").fetchone()
    if row:
        return row[0]
    cur = con.execute(
        "INSERT INTO activos (ticker, nombre, tipo, moneda_nominal) VALUES (?, ?, ?, ?);",
        ("USD_A3500", "Dólar A3500 (mayorista, ref. BCRA)", "fx", "USD"),
    )
    print("Activo USD_A3500 creado.")
    return cur.lastrowid


def actualizar(db_path: Path = DB_PATH, desde: str | None = None) -> dict:
    """Baja la serie del BCRA desde `desde` (None = completa) y la guarda.
    La descarga va antes de abrir la transaccion. Devuelve el resumen."""
    puntos = bcra_serie(BCRA_ID_A3500, desde)
    if not puntos:
        raise RuntimeError(f"el BCRA no devolvio datos de la variable {BCRA_ID_A3500} desde {desde}")

    con = sqlite3.connect(db_path, isolation_level=None)
    try:
        con.execute("BEGIN;")
        activo_id = _activo_a3500(con)
        existentes = dict(con.execute(
            "SELECT fecha, precio FROM precios WHERE activo_id = ? AND fecha >= ?;",
            (activo_id, min(f for f, _ in puntos)),
        ).fetchall())
        nuevas = actualizadas = 0
        for fecha, valor in puntos:
            viejo = existentes.get(fecha)
            if viejo is not None and round(viejo, 4) == round(valor, 4):
                continue
            con.execute(
                """
                INSERT INTO precios (activo_id, fecha, precio, moneda, fuente)
                VALUES (?, ?, ?, 'ARS', 'bcra')
                ON CONFLICT (activo_id, fecha) DO UPDATE SET precio = excluded.precio, fuente = 'bcra';
                """,
                (activo_id, fecha, valor),
            )
            if viejo is None:
                nuevas += 1
            else:
                actualizadas += 1
                print(f"  revisado {fecha}: {viejo} -> {valor}")
        con.execute("COMMIT;")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    finally:
        con.close()

    ultima = max(puntos)
    resumen = {"filas_bcra": len(puntos), "nuevas": nuevas, "actualizadas": actualizadas,
               "desde": min(puntos)[0], "ultima_fecha": ultima[0], "ultimo_valor": ultima[1]}
    print(f"USD_A3500: {resumen}")
    return resumen


def hacer_backup(db_path: Path) -> None:
    backup = db_path.parent / "backups" / f"pwos_backup_pre-a3500_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, backup)
    con = sqlite3.connect(backup)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup.name} (integrity_check=ok)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--backfill", action="store_true", help="serie completa desde 2002")
    a = ap.parse_args()
    hacer_backup(a.db)
    actualizar(a.db, None if a.backfill else (date.today() - timedelta(days=DIAS_VENTANA)).isoformat())
