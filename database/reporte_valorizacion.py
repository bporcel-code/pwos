"""
PWOS -- reporte de valorizacion posicion por posicion, para cruzar a mano
contra las apps reales de cada broker (sesion 2026-07-14, cierre de los
bugs de VN100 y de moneda ARS/USD; extendido 2026-07-22 con la seccion
de EFECTIVO para el cierre de la ronda incremental 2026-07-21/22).

Solo lectura: no inserta ni modifica nada en la base. Reusa
`valor_posicion_nativo` de `valorizacion.py` (mismo calculo que
`dashboard.py`) y la clasificacion de moneda ya resuelta en `precios`
(no reimplementa `MONEDA_PRECIO_DATA912` -- lee la moneda que quedo
guardada en `vista_precio_actual`, que es la fuente de verdad post-fix).
La seccion de EFECTIVO usa `vista_saldos_cuenta`, misma fuente que
`dashboard.py` -- el PATRIMONIO TOTAL de este reporte debe coincidir
exacto con el de `dashboard.py`.

Posiciones sin precio cargado (ej. una ON recien acreditada, sin panel
en data912 todavia) se listan igual, marcadas "sin precio" -- no se
omiten en silencio, para que quede claro que estan en cartera aunque no
sumen al total en dolares todavia.
"""

import sqlite3
from pathlib import Path

from valorizacion import valor_posicion_nativo, convertir_a_usd, convertir_efectivo_a_usd, pct_de, pct_propiedad_por_cuenta

DB_PATH = Path(__file__).parent / "pwos.db"


def obtener_precio_fx(con, ticker: str) -> float:
    row = con.execute("""
        SELECT p.precio FROM vista_precio_actual p
        JOIN activos a ON a.id = p.activo_id
        WHERE a.ticker = ?;
    """, (ticker,)).fetchone()
    if row is None:
        raise ValueError(f"No hay cotizacion de {ticker} cargada en 'precios'.")
    return row[0]


def construir_filas_efectivo(con, mep: float) -> list[dict]:
    """`cantidad` es el saldo de la app (100%); `valor_usd`, la parte propia (contenedores compartidos, ADR-032)."""
    pcts = pct_propiedad_por_cuenta(con)
    filas = []
    for cuenta_id, alias, moneda, estado, contenedor, saldo in con.execute(
        "SELECT * FROM vista_saldos_cuenta;"
    ):
        if moneda == "CARTERA":
            continue  # no es efectivo real, se valoriza via posiciones
        valor_usd = convertir_efectivo_a_usd(saldo, moneda, mep, pct_de(pcts, cuenta_id))
        filas.append({
            "contenedor": contenedor, "ticker": f"({estado})", "tipo": "efectivo",
            "cantidad": saldo, "precio": None, "moneda": moneda,
            "valor_usd": valor_usd,
        })
    filas.sort(key=lambda f: (f["contenedor"], f["moneda"]))
    return filas


def imprimir_reporte_efectivo(filas: list[dict]) -> float:
    print(f"{'CONTENEDOR':<20}{'TICKER':<14}{'TIPO':<12}{'CANTIDAD':>16}"
          f"{'COTIZACION':>12}{'MON':>9}{'TENENCIA USD':>16}")
    print("-" * 100)
    total = 0.0
    for f in filas:
        total += f["valor_usd"]
        print(f"{f['contenedor']:<20}{f['ticker']:<14}{f['tipo']:<12}"
              f"{f['cantidad']:>16.2f}{'-':>12}{f['moneda']:>9}"
              f"{f['valor_usd']:>16.2f}")
    print("-" * 100)
    print(f"{'TOTAL EFECTIVO':<80}{total:>20.2f}")
    return total


def construir_filas(con, mep: float, ccl: float) -> list[dict]:
    filas = []
    for activo_id, ticker, tipo, contenedor_id, contenedor, cantidad in con.execute(
        "SELECT * FROM vista_posiciones;"
    ):
        precio_row = con.execute(
            "SELECT precio, moneda FROM vista_precio_actual WHERE activo_id = ?;",
            (activo_id,),
        ).fetchone()
        if precio_row is None:
            filas.append({
                "contenedor": contenedor, "ticker": ticker, "tipo": tipo,
                "cantidad": cantidad, "precio": None, "moneda": None,
                "valor_usd": None,
            })
            continue
        precio, moneda = precio_row
        valor_nativo = valor_posicion_nativo(tipo, cantidad, precio)
        valor_usd = convertir_a_usd(tipo, valor_nativo, moneda, mep, ccl)
        filas.append({
            "contenedor": contenedor, "ticker": ticker, "tipo": tipo,
            "cantidad": cantidad, "precio": precio, "moneda": moneda,
            "valor_usd": valor_usd,
        })
    filas.sort(key=lambda f: (f["contenedor"], f["ticker"]))
    return filas


def imprimir_reporte_inversiones(filas: list[dict]) -> float:
    print(f"{'CONTENEDOR':<20}{'TICKER':<9}{'TIPO':<17}{'CANTIDAD':>14}"
          f"{'COTIZACION':>14}{'MON':>9}{'TENENCIA USD':>16}")
    print("-" * 100)

    total = 0.0
    for f in filas:
        if f["valor_usd"] is None:
            print(f"{f['contenedor']:<20}{f['ticker']:<9}{f['tipo']:<17}"
                  f"{f['cantidad']:>14.2f}{'sin precio':>14}{'-':>9}{'-':>16}")
            continue
        total += f["valor_usd"]
        print(f"{f['contenedor']:<20}{f['ticker']:<9}{f['tipo']:<17}"
              f"{f['cantidad']:>14.2f}{f['precio']:>14.2f}{f['moneda']:>9}"
              f"{f['valor_usd']:>16.2f}")

    print("-" * 100)
    print(f"{'TOTAL INVERSIONES':<80}{total:>20.2f}")
    return total


if __name__ == "__main__":
    con = sqlite3.connect(DB_PATH)
    mep = obtener_precio_fx(con, "USD_MEP")
    ccl = obtener_precio_fx(con, "USD_CCL")

    print("=" * 100)
    print(f"  PWOS -- Valorizacion posicion por posicion (MEP venta: {mep} · CCL venta: {ccl}, CEDEARs via CCL)")
    print("=" * 100)

    print("\n--- EFECTIVO ---")
    filas_efectivo = construir_filas_efectivo(con, mep)
    total_efectivo = imprimir_reporte_efectivo(filas_efectivo)

    print("\n--- INVERSIONES ---")
    filas_inversiones = construir_filas(con, mep, ccl)
    total_inversiones = imprimir_reporte_inversiones(filas_inversiones)

    print("\n" + "=" * 100)
    print(f"{'TOTAL EFECTIVO':<80}{total_efectivo:>20.2f}")
    print(f"{'TOTAL INVERSIONES':<80}{total_inversiones:>20.2f}")
    print(f"{'PATRIMONIO TOTAL':<80}{total_efectivo + total_inversiones:>20.2f}")
    print("=" * 100)

    con.close()
