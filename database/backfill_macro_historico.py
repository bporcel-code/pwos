"""
Backfill de `macro_historico` (Epica 12 Parte 4, 2026-09-12, pedido del
usuario): carga la serie historica COMPLETA de riesgo_pais, inflacion
(mensual + interanual) y REM (inflacion 12m + devaluacion esperada
12m) -- todo desde las mismas fuentes que `cargar_benchmarks_macro.py`
ya usa para el "ultimo valor" de `benchmarks_macro`, sin ninguna
integracion nueva.

Re-ejecutable: INSERT OR IGNORE sobre (clave, fecha) -- correr esto de
nuevo no duplica nada, solo agrega fechas que todavia no estuvieran.
Pensado como backfill de una sola vez; de ahi en mas
`cargar_benchmarks_macro.py` va agregando el dato del dia/mes nuevo en
cada corrida (ver `append_macro_historico` ahi).

`ccl_mep_brecha_pct` NO se backfillea aca -- se calcula al vuelo desde
`precios` (ver brecha_cambiaria.py). `inflacion_interanual` se carga
igual (fuera del enum de umbrales, contexto general).

Uso:
    python backfill_macro_historico.py
"""
import sqlite3
from pathlib import Path

import requests

from cargar_benchmarks_macro import (
    INFLACION_INTERANUAL_URL, INFLACION_MENSUAL_URL, RIESGO_PAIS_HIST_URL,
    obtener_rem_historico, rem_devaluacion_pct, _UA,
)
import configuracion

DB_PATH = configuracion.ruta_db()


def _filas_riesgo_pais() -> list[tuple[str, str, float]]:
    r = requests.get(RIESGO_PAIS_HIST_URL, headers=_UA, timeout=30)
    r.raise_for_status()
    return [("riesgo_pais", f["fecha"], float(f["valor"])) for f in r.json() if f.get("valor") is not None]


def _filas_inflacion(url: str, clave: str) -> list[tuple[str, str, float]]:
    r = requests.get(url, headers=_UA, timeout=30)
    r.raise_for_status()
    return [(clave, f["fecha"], float(f["valor"])) for f in r.json() if f.get("valor") is not None]


def _filas_rem() -> list[tuple[str, str, float]]:
    filas = []
    for rel in obtener_rem_historico():
        fecha = rel["relevamiento"]
        if rel.get("inflacion_prox12m_pct") is not None:
            filas.append(("rem_inflacion_12m", fecha, float(rel["inflacion_prox12m_pct"])))
        deval_pct = rem_devaluacion_pct(rel)
        if deval_pct is not None:
            filas.append(("rem_devaluacion_esperada_12m", fecha, deval_pct))
    return filas


def backfill(db_path: Path = DB_PATH) -> None:
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")

    fuentes = [
        ("riesgo_pais", _filas_riesgo_pais),
        ("inflacion_mensual", lambda: _filas_inflacion(INFLACION_MENSUAL_URL, "inflacion_mensual")),
        ("inflacion_interanual", lambda: _filas_inflacion(INFLACION_INTERANUAL_URL, "inflacion_interanual")),
        ("rem (inflacion + devaluacion esperada)", _filas_rem),
    ]

    try:
        con.execute("BEGIN;")
        total_insertadas = 0
        for nombre, fn in fuentes:
            filas = fn()
            cur = con.executemany(
                "INSERT OR IGNORE INTO macro_historico (clave, fecha, valor) VALUES (?, ?, ?);",
                filas,
            )
            print(f"{nombre}: {len(filas)} filas descargadas, {cur.rowcount if cur.rowcount >= 0 else '?'} nuevas insertadas (resto ya existia)")
            total_insertadas += 1

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nBackfill OK.")

        for clave in ("riesgo_pais", "inflacion_mensual", "inflacion_interanual",
                      "rem_inflacion_12m", "rem_devaluacion_esperada_12m"):
            n, mn, mx = con.execute(
                "SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM macro_historico WHERE clave = ?;", (clave,)
            ).fetchone()
            print(f"  {clave}: {n} filas ({mn} a {mx})")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"BACKFILL ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    backfill()
