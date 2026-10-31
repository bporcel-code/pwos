"""
Alta de un split / contrasplit (`eventos_corporativos`, schema v2.48).

La cantidad antes/despues es la que registro el broker (puede no ser el
ratio exacto: un contrasplit 3:1 puede dejar 5 cuotapartes en 2). Valida que la
tenencia del activo en ese contenedor justo ANTES de la fecha (operaciones
con fecha anterior + eventos anteriores) sea `cantidad_antes`; si no, aborta.
No toca el costo ni mueve efectivo (ver migrar_v248_eventos_corporativos.py).

Uso (parado en database/):
    python cargar_evento_corporativo.py SPY "Broker A" 2026-10-07 contrasplit 3 1 5 2 \
        "Contrasplit 3:1 del ETF; el broker registro OUT 3 (5 -> 2)" [--db COPIA.db]
"""
import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()


def tenencia_antes(con, activo_id: int, contenedor_id: int, fecha: str) -> float:
    """Tenencia en el contenedor justo antes de `fecha` (misma regla de signo
    que vista_posiciones, + eventos anteriores)."""
    ops = con.execute(
        """SELECT COALESCE(SUM(CASE WHEN m.tipo = 'compra_activo' THEN oa.cantidad ELSE -oa.cantidad END), 0)
           FROM operaciones_activos oa JOIN movimientos m ON m.id = oa.movimiento_id
           WHERE oa.activo_id = ? AND oa.contenedor_id = ? AND m.fecha < ?""",
        (activo_id, contenedor_id, fecha)).fetchone()[0]
    eventos = con.execute(
        """SELECT COALESCE(SUM(cantidad_despues - cantidad_antes), 0) FROM eventos_corporativos
           WHERE activo_id = ? AND contenedor_id = ? AND fecha < ?""",
        (activo_id, contenedor_id, fecha)).fetchone()[0]
    return round(ops + eventos, 6)


def cargar(con, ticker: str, contenedor: str, fecha: str, tipo: str, ratio_viejas: float, ratio_nuevas: float,
           cantidad_antes: float, cantidad_despues: float, descripcion: str) -> int:
    """Inserta el evento (sin transaccion propia). Devuelve su id."""
    activo = con.execute("SELECT id FROM activos WHERE ticker = ?", (ticker,)).fetchone()
    cont = con.execute("SELECT id FROM contenedores WHERE nombre = ?", (contenedor,)).fetchone()
    if activo is None or cont is None:
        raise RuntimeError(f"no existe el activo {ticker!r} o el contenedor {contenedor!r}")
    previa = tenencia_antes(con, activo[0], cont[0], fecha)
    if abs(previa - cantidad_antes) > 1e-6:
        raise RuntimeError(f"{ticker} en {contenedor}: la tenencia antes del {fecha} es {previa}, no {cantidad_antes}")
    return con.execute(
        """INSERT INTO eventos_corporativos (activo_id, contenedor_id, fecha, tipo, ratio_viejas, ratio_nuevas,
                                             cantidad_antes, cantidad_despues, descripcion)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (activo[0], cont[0], fecha, tipo, ratio_viejas, ratio_nuevas, cantidad_antes, cantidad_despues,
         descripcion)).lastrowid


def main() -> None:
    ap = argparse.ArgumentParser()
    for nombre in ("ticker", "contenedor", "fecha", "tipo"):
        ap.add_argument(nombre)
    for nombre in ("ratio_viejas", "ratio_nuevas", "cantidad_antes", "cantidad_despues"):
        ap.add_argument(nombre, type=float)
    ap.add_argument("descripcion")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    a = ap.parse_args()
    backup = a.db.parent / "backups" / f"pwos_backup_pre-evento-corporativo_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(a.db, backup)
    print(f"Backup: {backup.name}")
    con = sqlite3.connect(a.db, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    try:
        con.execute("BEGIN;")
        evento_id = cargar(con, a.ticker, a.contenedor, a.fecha, a.tipo, a.ratio_viejas, a.ratio_nuevas,
                           a.cantidad_antes, a.cantidad_despues, a.descripcion)
        if con.execute("PRAGMA foreign_key_check;").fetchall():
            raise RuntimeError("foreign_key_check encontro filas rotas")
        con.execute("COMMIT;")
        print(f"Evento {evento_id} cargado: {a.ticker} {a.contenedor} {a.fecha} {a.cantidad_antes:g} -> "
              f"{a.cantidad_despues:g}")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
