"""
PWOS -- Dry-run de la carga de cronograma de pagos + referencia (ley,
tipo de instrumento) de bonos desde Rava. Solo lectura, no toca
pwos.db -- mismo patron que el resto de los dry_run_*.py del proyecto
(los dry runs de cada broker).

Fuente de tickers: vista_posiciones de pwos.db (renta fija vigente),
NO una lista hardcodeada -- asi el batch queda vigente solo cuando se
compra un bono nuevo, sin necesidad de tocar este script.
"""
import sqlite3
from pathlib import Path

from rava_client import obtener_flujo_bono, obtener_referencia_bonos

DB_PATH = Path(__file__).parent / "pwos.db"

TIPOS_RENTA_FIJA = ("bono_soberano", "bono_provincial", "bono_cer", "on", "lecap")


def tickers_renta_fija_vigente() -> list[str]:
    con = sqlite3.connect(DB_PATH)
    try:
        filas = con.execute(
            f"""
            SELECT DISTINCT ticker FROM vista_posiciones
            WHERE tipo_activo IN ({",".join("?" * len(TIPOS_RENTA_FIJA))})
            ORDER BY ticker;
            """,
            TIPOS_RENTA_FIJA,
        ).fetchall()
        return [f[0] for f in filas]
    finally:
        con.close()


def main():
    tickers = tickers_renta_fija_vigente()
    print(f"Tickers de renta fija vigentes en pwos.db: {len(tickers)}")
    print(tickers)
    print()

    referencia = obtener_referencia_bonos()
    print(f"Listado de Rava (/api/prices/bonos): {len(referencia)} especies en total.\n")

    ok, sin_referencia, sin_flujo = [], [], []

    for ticker in tickers:
        ref = referencia.get(ticker)
        flujo = obtener_flujo_bono(ticker)

        if ref is None:
            sin_referencia.append(ticker)
        if flujo is None:
            sin_flujo.append(ticker)

        if ref is None or flujo is None:
            print(f"{ticker:8} FALTA -- referencia={'si' if ref else 'NO'}  flujo={'si' if flujo else 'NO'}")
            continue

        n_pagos_futuros = sum(1 for f in flujo["flujos"] if f["tipo"] == "futuro")
        n_pagos_totales = len(flujo["flujos"])
        print(
            f"{ticker:8} OK    tipo={ref['tipo']:<16} ley={ref['ley']:<4} "
            f"pagos_totales={n_pagos_totales:<3} pagos_futuros={n_pagos_futuros:<3} "
            f"nombre={ref['nombre'][:45]}"
        )
        ok.append(ticker)

    print()
    print("=" * 60)
    print(f"OK (referencia + flujo completos): {len(ok)}/{len(tickers)}")
    if sin_referencia:
        print(f"SIN referencia en Rava (revisar manual / Sheet): {sin_referencia}")
    if sin_flujo:
        print(f"SIN flujo en Rava (revisar manual / Sheet): {sin_flujo}")


if __name__ == "__main__":
    main()
