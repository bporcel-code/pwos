"""
PWOS -- reporte de consola de Herramienta 1 (Epica 11: rotacion de
bonos por compresion de TIR). Solo lectura, para que el usuario corrobore
los numeros contra lo que sabe de cada posicion antes de wire-earlo a
la API/dashboard.

Uso:
    python reporte_rotacion_bonos.py
"""
import sqlite3
from pathlib import Path

from dashboard import obtener_precio_fx
from tir_bonos import rotacion_bonos
import configuracion

DB_PATH = configuracion.ruta_db()


def imprimir(filas: list[dict]) -> None:
    calculables = [f for f in filas if "compresion_pp" in f]
    sin_calcular = [f for f in filas if "compresion_pp" not in f]

    calculables.sort(key=lambda f: f["compresion_pp"], reverse=True)

    print(f"{'TICKER':<7} {'CANTIDAD':>12} {'MONEDA':>6} {'ENTRADA':>9} {'HOY':>9} {'COMPR.':>8} {'USD':>10}  {'ROTAR?':<6} {'VERIF.'}")
    print("-" * 95)
    for f in calculables:
        marca = "SI *" if f["candidato_a_rotar"] else "  no"
        verif = "OK" if f["entrada_verificable"] else "revisar entrada"
        print(f"{f['ticker']:<7} {f['cantidad']:>12,.0f} {f['moneda']:>6} "
              f"{f['tir_entrada_pct']:>8.2f}% {f['tir_hoy_pct']:>8.2f}% "
              f"{f['compresion_pp']:>+7.2f}p {f['valorizacion_usd']:>10,.2f}  {marca:<6}  {verif}")
        if f["candidato_a_rotar"] and f["candidatos_reemplazo"]:
            for c in f["candidatos_reemplazo"]:
                print(f"        -> {c['ticker']:<7} TIR {c['tir_pct']:>6.2f}%  "
                      f"duration {c['duration']:>5.2f}a  paridad {c['paridad_pct']:>6.2f}%  vence {c['vencimiento']}")
        elif f["candidato_a_rotar"]:
            print("        (sin candidatos de reemplazo con legislacion/duration comparable y dato consistente)")

    if sin_calcular:
        print()
        print("Sin TIR calculable:")
        for f in sin_calcular:
            print(f"  {f['ticker']:<7} -- {f['motivo']}")

    total_usd = sum(f["valorizacion_usd"] for f in calculables)
    candidatos_rotar = [f for f in calculables if f["candidato_a_rotar"]]
    print()
    print(f"Total valorizado (bonos con TIR calculable): USD {total_usd:,.2f}")
    print(f"Candidatos a rotar (compresion > umbral): {len(candidatos_rotar)} de {len(calculables)}")
    if any(not f["entrada_verificable"] for f in calculables):
        print("NOTA: 'revisar entrada' = hubo una venta parcial de esta posicion en algun contenedor,")
        print("      el promedio ponderado de entrada de este calculo v1 no la contempla.")


if __name__ == "__main__":
    con = sqlite3.connect(DB_PATH)
    mep = obtener_precio_fx(con, "USD_MEP")
    ccl = obtener_precio_fx(con, "USD_CCL")
    filas = rotacion_bonos(con, mep, ccl, umbral_compresion_pp=3.0)
    imprimir(filas)
