"""
Backfill de la tasa libre de riesgo (T-Bill 13 semanas, ^IRX de yfinance)
para la frontera eficiente de Renta Variable (Epica 19, Fase 5,
2026-09-23). Carga la serie DIARIA desde 2010-01-01 en `macro_historico`
(clave `tbill_3m`, INSERT OR IGNORE, re-ejecutable) y el ultimo valor en
`benchmarks_macro` (clave `tbill_3m_pct`). A partir de aca
`cargar_benchmarks_macro.py` la mantiene al dia.

Escala: ^IRX ya viene en % (verificada contra referencias historicas, ver
obtener_tbill_3m en cargar_benchmarks_macro.py). Aborta si algun valor cae
fuera de [-1, 20] % (senal de cambio de convencion de yfinance).

Es lo UNICO que toca la base en la Fase 5. Backup previo + transaccion +
integrity/foreign_key check.

Uso (parado en database/):
    python backfill_tbill_3m_2026-09-23.py
"""
import sqlite3
from datetime import datetime
from pathlib import Path

import yfinance as yf

from cargar_benchmarks_macro import hacer_backup, obtener_tbill_3m, upsert_benchmark
import configuracion

DB_PATH = configuracion.ruta_db()
DESDE = "2010-01-01"

hist = yf.Ticker("^IRX").history(period="max", auto_adjust=False)["Close"].dropna()
hist = hist[hist.index >= DESDE]
if hist.empty or hist.min() < -1 or hist.max() > 20:
    raise RuntimeError(f"^IRX fuera de escala esperada: min={hist.min()}, max={hist.max()} -- revisar a mano")
filas = [("tbill_3m", ts.date().isoformat(), round(float(v), 4)) for ts, v in hist.items()]
print(f"^IRX: {len(filas)} dias, {filas[0][1]} a {filas[-1][1]}, rango {hist.min():.3f}%..{hist.max():.3f}%")

hacer_backup(DB_PATH)
con = sqlite3.connect(DB_PATH, isolation_level=None)
con.execute("PRAGMA foreign_keys = ON;")
try:
    con.execute("BEGIN;")
    antes = con.execute("SELECT COUNT(*) FROM macro_historico WHERE clave='tbill_3m';").fetchone()[0]
    con.executemany("INSERT OR IGNORE INTO macro_historico (clave, fecha, valor) VALUES (?, ?, ?);", filas)
    despues = con.execute("SELECT COUNT(*) FROM macro_historico WHERE clave='tbill_3m';").fetchone()[0]
    upsert_benchmark(con, "tbill_3m_pct", obtener_tbill_3m(), datetime.now().isoformat())
    ic = con.execute("PRAGMA integrity_check;").fetchone()[0]
    fk = con.execute("PRAGMA foreign_key_check;").fetchall()
    if ic != "ok" or fk:
        raise RuntimeError(f"integrity_check={ic}, foreign_key_check={fk}")
    con.execute("COMMIT;")
    print(f"macro_historico tbill_3m: {antes} -> {despues} filas. COMMIT OK.")
except Exception:
    con.execute("ROLLBACK;")
    raise
finally:
    con.close()
