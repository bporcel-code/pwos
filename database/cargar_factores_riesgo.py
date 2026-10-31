"""
PWOS -- Carga factores_riesgo (beta acciones/CEDEARs, Deuda/Patrimonio
ONs) desde yfinance, para el scorecard de riesgo de cartera
(database/riesgo.py, ver ESTADO_PWOS.md seccion "Metodologia de riesgo
de cartera").

Re-ejecutable (UPSERT, no INSERT ciego), mismo patron que
cargar_bonos_referencia_flujo.py: correr esto de nuevo (semanal via n8n
cuando se sume ese endpoint, igual que el refresco de Rava) actualiza
el registro existente. Tolerante a fallos por ticker -- si yfinance no
resuelve un simbolo, se loguea NULL y se sigue con el resto.

`debtToEquity` de yfinance viene en formato "porcentaje" (ej. 69.055 =
ratio 0.69) -- se divide por 100 ACA, antes de guardarlo, para que
riesgo.score_deuda_patrimonio (cortes <1/1-2/>2) reciba el ratio real,
no el numero crudo de Yahoo.
"""
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from clasificar_yfinance import simbolo_yfinance
from fetch_yfinance import fetch_yfinance_info
from riesgo import ON_EMISOR_YFINANCE
import configuracion

DB_PATH = configuracion.ruta_db()


def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-factores-riesgo_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


def tickers_equity_vigente(con) -> list[tuple[int, str, str]]:
    """(activo_id, ticker, tipo_activo) para acciones/CEDEARs con posicion vigente."""
    filas = con.execute("""
        SELECT a.id, a.ticker, vp.tipo_activo
        FROM vista_posiciones vp
        JOIN activos a ON a.ticker = vp.ticker
        WHERE vp.tipo_activo IN ('accion', 'cedear') AND vp.cantidad != 0
        GROUP BY a.id;
    """).fetchall()
    return filas


def tickers_on_vigente(con) -> list[tuple[int, str]]:
    """(activo_id, ticker) para ONs con posicion vigente."""
    filas = con.execute("""
        SELECT a.id, a.ticker
        FROM vista_posiciones vp
        JOIN activos a ON a.ticker = vp.ticker
        WHERE vp.tipo_activo = 'on' AND vp.cantidad != 0
        GROUP BY a.id;
    """).fetchall()
    return filas


def _upsert(con, activo_id: int, beta: float | None, deuda_patrimonio: float | None, ahora: str) -> None:
    con.execute(
        """
        INSERT INTO factores_riesgo (activo_id, beta, deuda_patrimonio, fuente, actualizado_en)
        VALUES (?, ?, ?, 'yfinance', ?)
        ON CONFLICT (activo_id) DO UPDATE SET
            beta = excluded.beta,
            deuda_patrimonio = excluded.deuda_patrimonio,
            fuente = 'yfinance',
            actualizado_en = excluded.actualizado_en;
        """,
        (activo_id, beta, deuda_patrimonio, ahora),
    )


def cargar(db_path: Path = DB_PATH) -> None:
    hacer_backup(db_path)

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    ahora = datetime.now(timezone.utc).isoformat()

    try:
        con.execute("BEGIN;")

        print("=== Acciones / CEDEARs (beta) ===")
        for activo_id, ticker, tipo_activo in tickers_equity_vigente(con):
            sym = simbolo_yfinance(ticker, tipo_activo)
            info = fetch_yfinance_info(sym)
            beta = info.get("beta") if info else None
            _upsert(con, activo_id, beta, None, ahora)
            print(f"{ticker:8} sym={sym:8} beta={beta}")

        print()
        print("=== ONs (Deuda/Patrimonio) ===")
        for activo_id, ticker in tickers_on_vigente(con):
            emisor_sym = ON_EMISOR_YFINANCE.get(ticker)
            deuda_patrimonio = None
            if emisor_sym is None:
                print(f"{ticker:8} sin emisor cotizante mapeado (privada) -- deuda_patrimonio queda NULL")
            else:
                info = fetch_yfinance_info(emisor_sym)
                dte = info.get("debtToEquity") if info else None
                deuda_patrimonio = dte / 100 if dte is not None else None
                print(f"{ticker:8} emisor={emisor_sym:8} debtToEquity_crudo={dte}  ratio={deuda_patrimonio}")
            _upsert(con, activo_id, None, deuda_patrimonio, ahora)

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nCarga OK. foreign_key_check: sin filas rotas")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    cargar()
