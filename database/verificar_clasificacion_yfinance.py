"""
PWOS -- Verificacion en vivo de clasificar_yfinance.py contra los
tickers reales de la cartera vigente (accion/cedear). Solo lectura, no
toca pwos.db. Corre la regla de aceptacion completa (resolucion de
simbolo + sufijo .BA + cross-check de nombre) y reporta el estado final
por ticker, para confirmar antes de escribir el loader real.
"""
import logging
import sqlite3
from pathlib import Path

from clasificar_yfinance import simbolo_yfinance, clasificar
from fetch_yfinance import fetch_yfinance_info

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

DB_PATH = Path(__file__).parent / "pwos.db"


def tickers_vigentes(tipo: str) -> list[str]:
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT DISTINCT ticker FROM vista_posiciones WHERE tipo_activo = ? ORDER BY ticker;",
            (tipo,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        con.close()


def main():
    activos = [(t, "cedear") for t in tickers_vigentes("cedear")]
    activos += [(t, "accion") for t in tickers_vigentes("accion")]

    resultados = []
    for ticker, tipo_activo in activos:
        simbolo = simbolo_yfinance(ticker, tipo_activo)
        info = fetch_yfinance_info(simbolo)
        resultado = clasificar(ticker, info)
        resultado["tipo_activo"] = tipo_activo
        resultado["simbolo_consultado"] = simbolo
        resultados.append(resultado)

    print(f"\n{'ticker':<8}{'tipo':<8}{'simbolo':<10}{'estado':<20}{'detalle'}")
    print("-" * 90)
    for r in resultados:
        if r["estado"] == "aceptado":
            detalle = f"{r['long_name']} | beta={r['beta']} | {r['sector']}/{r['country']}"
        elif r["estado"] == "etf":
            detalle = f"{r['long_name']} | category={r['category']} | fund_family={r['fund_family']}"
        else:
            detalle = r.get("motivo", "")
        print(f"{r['ticker']:<8}{r['tipo_activo']:<8}{r['simbolo_consultado']:<10}{r['estado']:<20}{detalle}")

    conteo = {}
    for r in resultados:
        conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
    print("\nResumen:", conteo)


if __name__ == "__main__":
    main()
