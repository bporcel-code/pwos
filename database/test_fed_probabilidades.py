"""
Casos de prueba de cargar_benchmarks_macro.probabilidades_proxima_reunion
(arreglo A, 2026-09-30). Sin red.

Uso:
    python test_fed_probabilidades.py
"""
from datetime import date

from cargar_benchmarks_macro import probabilidades_proxima_reunion

# Precios de liquidacion reales de CME del 2026-09-29.
SETTLE = {"SEP 26": 96.255, "OCT 26": 96.11, "NOV 26": 95.995, "DEC 26": 95.845}


def test_caso_real_del_bug():
    # La libreria usaba el promedio de septiembre (3,745 %, mes con la reunion
    # del 16-09) como tasa previa y daba 4,50-5,00 %. Con la EFFR vigente:
    r = probabilidades_proxima_reunion(SETTLE, date(2026, 10, 28), 3.88, 3.75)
    assert r["probabilidades"] == {"3.75%-4.00%": 69.0, "4.00%-4.25%": 31.0}, r
    assert r["target_actual"] == "3.75%-4.00%" and r["sesgo"] == "sin cambios"
    assert all(k < "4.50" for k in r["probabilidades"])


def test_recorte():
    # Mercado que descuenta un recorte de 25 pb casi seguro el 28-10.
    settle = {"OCT 26": 100 - (3.88 * 27 + 3.63 * 4) / 31}
    r = probabilidades_proxima_reunion(settle, date(2026, 10, 28), 3.88, 3.75)
    assert max(r["probabilidades"], key=r["probabilidades"].get) == "3.50%-3.75%", r
    assert r["sesgo"] == "recorte"


def test_reunion_a_fin_de_mes_usa_el_mes_siguiente():
    # Reunion el 29-12: quedan 3 dias -> se usa el futuro de enero entero.
    settle = {"DEC 26": 96.10, "JAN 27": 96.37}               # enero = 3,63 %
    r = probabilidades_proxima_reunion(settle, date(2026, 12, 29), 3.88, 3.75)
    assert r["probabilidades"] == {"3.50%-3.75%": 100.0}, r
    assert r["tasa_implicita_post"] == 3.63


def test_sin_futuro_del_mes():
    assert probabilidades_proxima_reunion({"NOV 26": 96.0}, date(2026, 10, 28), 3.88, 3.75) is None


def main():
    pruebas = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for p in pruebas:
        p()
        print(f"  OK  {p.__name__}")
    print(f"\nOK -- {len(pruebas)} casos de prueba de las probabilidades de la Fed")


if __name__ == "__main__":
    main()
