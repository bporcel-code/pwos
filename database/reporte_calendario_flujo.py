"""
PWOS -- reporte de consola de Herramienta 2, paso 1 (Epica 11:
calendario de flujo mensual y huecos de renta). Solo lectura.

Uso:
    python reporte_calendario_flujo.py
"""
import sqlite3
from pathlib import Path

from calendario_flujo import calendario_flujo_mensual
from dashboard import obtener_precio_fx
import configuracion

DB_PATH = configuracion.ruta_db()

if __name__ == "__main__":
    con = sqlite3.connect(DB_PATH)
    mep = obtener_precio_fx(con, "USD_MEP")
    resultado = calendario_flujo_mensual(con, mep, meses=12)

    print(f"Promedio mensual (ventana de 12 meses): USD {resultado['promedio_usd']:,.2f}")
    print(f"Umbral de mes flojo (50% del promedio): USD {resultado['umbral_usd']:,.2f}")
    print()
    print(f"{'MES':<8} {'INTERES':>10} {'AMORTIZ.':>10} {'OTROS':>10} {'TOTAL':>10}  {'ESTIMADO?':<10} {'ESTADO'}")
    print("-" * 80)
    for f in resultado["serie"]:
        estado = "*** MES FLOJO ***" if f["mes_flojo"] else "cubierto"
        estimado = "si (MEP hoy)" if f["tiene_estimado"] else "no"
        print(f"{f['mes']:<8} {f['interes_usd']:>10,.2f} {f['amortizacion_usd']:>10,.2f} "
              f"{f['otros_usd']:>10,.2f} {f['total_usd']:>10,.2f}  {estimado:<10} {estado}")
        for d in f["detalle"]:
            marca = " (est.)" if d["es_estimado"] else ""
            print(f"    {d['fecha']}  {d['tipo']:<12} {d['concepto']:<10} USD {d['monto_usd']:>10,.2f}{marca}")

    flojos = [f["mes"] for f in resultado["serie"] if f["mes_flojo"]]
    print()
    print(f"Meses flojos: {len(flojos)} de {len(resultado['serie'])} -> {flojos}")
