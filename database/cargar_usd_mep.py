import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pwos.db"
con = sqlite3.connect(DB_PATH)

con.execute(
    "INSERT INTO activos (ticker, nombre, tipo, moneda_nominal) VALUES (?, ?, ?, ?);",
    ("USD_MEP", "Dólar MEP", "fx", "USD"),
)
con.commit()
print("✅ Activo USD_MEP creado.")

con.close()