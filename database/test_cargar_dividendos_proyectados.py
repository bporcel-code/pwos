"""
Tests de `armar_cronograma` (cargar_dividendos_proyectados.py): funcion pura,
sin red. Casos reales tomados de yfinance el 2026-10-05.

Uso:  python -m pytest test_cargar_dividendos_proyectados.py -q
"""
from datetime import date

from cargar_dividendos_proyectados import armar_cronograma

HOY = date(2026, 10, 5)
PEP = [(date(2025, 12, 5), 1.423), (date(2026, 3, 6), 1.423), (date(2026, 6, 5), 1.48), (date(2026, 9, 4), 1.48)]


def test_trimestral_con_calendario_ya_pasado():
    # El calendario trae el ultimo (ya pagado): solo define la demora ex->pago (26 dias).
    cal = {"Ex-Dividend Date": date(2026, 9, 3), "Dividend Date": date(2026, 9, 29)}
    ev = armar_cronograma(PEP, cal, "USD", HOY)
    assert ev[0] == {"fecha_ex": date(2026, 9, 4), "fecha_pago": date(2026, 10, 2),
                     "monto_por_accion": 1.48, "tipo": "historico"}  # real: cobrado el 01/10
    assert [e["tipo"] for e in ev[1:]] == ["proyectado"] * (len(ev) - 1)
    assert ev[1]["fecha_ex"] == date(2026, 12, 4)
    assert all(e["monto_por_accion"] == 1.48 for e in ev)


def test_anunciado():
    msft = [(date(2026, 2, 19), 0.91), (date(2026, 5, 21), 0.91), (date(2026, 8, 20), 0.91)]
    cal = {"Ex-Dividend Date": date(2026, 11, 18), "Dividend Date": date(2026, 12, 9)}
    ev = armar_cronograma(msft, cal, "USD", HOY)
    anunciado = [e for e in ev if e["tipo"] == "anunciado"]
    assert anunciado == [{"fecha_ex": date(2026, 11, 18), "fecha_pago": date(2026, 12, 11),
                          "monto_por_accion": 0.91, "tipo": "anunciado"}]
    # el pago historico del 20/08 ya se acredito antes de octubre: no figura
    assert not [e for e in ev if e["tipo"] == "historico"]


def test_accion_local_anual():
    irsa = [(date(2024, 11, 1), 126.1171), (date(2025, 11, 3), 224.8411)]
    ev = armar_cronograma(irsa, {}, "ARS", HOY)
    assert ev[0]["fecha_ex"] == date(2026, 11, 3) and ev[0]["fecha_pago"] == date(2026, 11, 5)
    assert ev[0]["monto_por_accion"] == 224.8411


def test_sin_dividendos_recientes_no_inventa():
    assert armar_cronograma([(date(2022, 10, 25), 5.0)], {}, "ARS", HOY) == []
    assert armar_cronograma([], {}, "USD", HOY) == []
