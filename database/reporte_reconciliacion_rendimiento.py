"""
PWOS -- Reporte de reconciliacion: cierres mensuales manuales (el usuario,
`cierres_mensuales`, fuente real del grafico de Rendimiento desde
2026-09-03) vs. la reconstruccion ledger+precios (`serie_rendimiento`,
rendimiento.py -- ya no la usa el dashboard, ver ESTADO_PWOS.md punto 4
de la sesion 2026-09-13).

Objetivo: para cada mes con cierre manual cargado, calcular el
patrimonio que el ledger (movimientos + precios) reconstruye para ESA
MISMA fecha y compararlo contra el numero que el usuario cargo a mano. Si
coinciden (dentro de una tolerancia chica), el ledger esta validado
para ese mes. Si el ledger no puede reconstruir la fecha (falta precio
de algun activo vigente o el MEP/CCL de ese dia), se lista el motivo en
vez de comparar un numero parcial.

Solo LECTURA -- no escribe nada en la base, no requiere backup.

Convencion de fecha (a confirmar con el usuario si algun mes da una
diferencia sospechosamente parecida a un mes completo de rendimiento):
`cierres_mensuales.patrimonio_total_usd` de la fila (anio, mes) se
interpreta como el patrimonio al CIERRE (ultimo dia) de ese mes -- no
el primero, a diferencia de como `serie_rendimiento_mensual` etiqueta
sus tramos con el dia 1 (esa funcion solo necesita una fecha para
contar dias entre cierres consecutivos, no le importa el dia exacto).
Aca si importa: comparamos contra la reconstruccion del ledger en el
DIA REAL que el numero manual representa.

Uso:
    python reporte_reconciliacion_rendimiento.py
"""
import calendar
import sqlite3
from pathlib import Path

from rendimiento import patrimonio_a_fecha
import configuracion

DB_PATH = configuracion.ruta_db()

# Diferencia relativa a partir de la cual un mes "reconciliable" se
# marca como sospechoso en vez de simplemente OK -- valor chico, pensado
# para ruido de redondeo/conversion, no para esconder discrepancias
# reales.
TOLERANCIA_PCT = 0.5


def fecha_fin_de_mes(anio: int, mes: int) -> str:
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return f"{anio:04d}-{mes:02d}-{ultimo_dia:02d}"


def main():
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    filas = con.execute(
        "SELECT anio, mes, patrimonio_total_usd FROM cierres_mensuales ORDER BY anio, mes;"
    ).fetchall()

    print(f"{'Mes':<10} {'Manual USD':>14} {'Ledger USD':>14} {'Diff USD':>12} {'Diff %':>8}  Estado")
    print("-" * 90)

    reconciliables = []
    incompletos = []

    for anio, mes, patrimonio_manual in filas:
        fecha = fecha_fin_de_mes(anio, mes)
        ledger = patrimonio_a_fecha(con, fecha)
        etiqueta_mes = f"{anio}-{mes:02d}"

        if not ledger["completo"]:
            incompletos.append((etiqueta_mes, fecha, ledger["faltantes"]))
            print(f"{etiqueta_mes:<10} {patrimonio_manual:>14,.2f} {'--':>14} {'--':>12} {'--':>8}  incompleto ({len(ledger['faltantes'])} faltante(s))")
            continue

        patrimonio_ledger = ledger["total_usd"]
        diff = patrimonio_ledger - patrimonio_manual
        diff_pct = (diff / patrimonio_manual * 100) if patrimonio_manual else None
        estado = "OK" if diff_pct is not None and abs(diff_pct) <= TOLERANCIA_PCT else "REVISAR"
        reconciliables.append((etiqueta_mes, patrimonio_manual, patrimonio_ledger, diff, diff_pct, estado))
        print(f"{etiqueta_mes:<10} {patrimonio_manual:>14,.2f} {patrimonio_ledger:>14,.2f} {diff:>12,.2f} {diff_pct:>7.2f}%  {estado}")

    print("-" * 90)
    print(f"\nMeses reconciliables: {len(reconciliables)}/{len(filas)}. Incompletos (ledger sin foto completa): {len(incompletos)}/{len(filas)}.")

    if incompletos:
        print("\nDetalle de meses incompletos (motivo, primeros 3 por mes):")
        for etiqueta_mes, fecha, faltantes in incompletos:
            print(f"  {etiqueta_mes} ({fecha}):")
            for motivo in faltantes[:3]:
                print(f"    - {motivo}")
            if len(faltantes) > 3:
                print(f"    ... y {len(faltantes) - 3} mas")

    sospechosos = [r for r in reconciliables if r[5] == "REVISAR"]
    if sospechosos:
        print(f"\n{len(sospechosos)} mes(es) reconciliable(s) con diferencia > {TOLERANCIA_PCT}% -- revisar:")
        for etiqueta_mes, manual, ledger_val, diff, diff_pct, _ in sospechosos:
            print(f"  {etiqueta_mes}: manual={manual:,.2f} ledger={ledger_val:,.2f} diff={diff:,.2f} ({diff_pct:.2f}%)")

    con.close()


if __name__ == "__main__":
    main()
