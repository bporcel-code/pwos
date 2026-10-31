"""
Tests de `transferencias.verificar_transferencias_propias`.
Base en memoria con lo minimo (cuentas + movimientos); datos ficticios y
patrones de cuenta propia de prueba (no los de la configuracion).

  A. Montos repetidos: billetera A -50.000 -> banco +50.000 y banco -50.000 ->
     billetera B +50.000 (manual, "cuenta propia"). El egreso de la billetera A
     puede ir a cualquiera de los 2 ingresos; tiene que emparejar a todos.
  B. Una pata sin contraparte -> aparece en sin_pareja.
  C. La contraparte en el MISMO contenedor no vale.

Uso:  python -m pytest test_transferencias_propias.py -q
"""
import sqlite3

import pytest

import transferencias
from transferencias import verificar_transferencias_propias

BANCO, BILLETERA_A, BILLETERA_B = 1, 2, 3
CUIT, NOMBRE = "99111111112", "JUAN PEREZ"


@pytest.fixture(autouse=True)
def patrones_de_prueba(monkeypatch):
    monkeypatch.setattr(transferencias, "PATRONES_CUENTA_PROPIA",
                        [f"%{CUIT}%", "Transferencia enviada PEREZ%", "%(cuenta propia)%"])


def _con(*movs):
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE cuentas (id INTEGER PRIMARY KEY, contenedor_id INTEGER);
        INSERT INTO cuentas VALUES (1, 1), (2, 2), (3, 3);
        CREATE TABLE movimientos (id INTEGER PRIMARY KEY, fecha TEXT, tipo TEXT, cuenta_origen_id INTEGER,
            cuenta_destino_id INTEGER, monto REAL, moneda TEXT, descripcion TEXT, naturaleza TEXT DEFAULT 'efectivo');
    """)
    for fecha, tipo, cont, monto, desc in movs:
        origen, destino = (cont, None) if tipo == "egreso" else (None, cont)
        con.execute("INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, cuenta_destino_id, monto, moneda, "
                    "descripcion) VALUES (?, ?, ?, ?, ?, 'ARS', ?)", (fecha, tipo, origen, destino, monto, desc))
    return con


CASO_MONTOS_REPETIDOS = [
    ("2026-10-06", "egreso", BILLETERA_A, 50000.0, "Transferencia enviada PEREZ"),
    ("2026-10-06", "egreso", BANCO, 50000.0, f"TRANSF.HB.E / {CUIT}-{NOMBRE}-VAR"),
    ("2026-10-06", "ingreso", BILLETERA_B, 50000.0, "Transferencia desde el banco (cuenta propia)"),
    ("2026-10-06", "ingreso", BANCO, 50000.0, f"Credito Transferencia / {CUIT}-{NOMBRE}-VAR"),
]


def test_a_montos_repetidos_empareja_a_todos():
    r = verificar_transferencias_propias(_con(*CASO_MONTOS_REPETIDOS), "2026-10-01", "2026-10-09")
    assert r["sin_pareja"] == []
    pares = {(e[0], i[0]) for e, i in r["pares"]}
    assert len(pares) == 2
    # billetera A (id 1) -> banco (id 4) y banco (id 2) -> billetera B (id 3): la unica asignacion que cierra
    assert pares == {(1, 4), (2, 3)}


def test_b_pata_sin_contraparte():
    r = verificar_transferencias_propias(
        _con(*CASO_MONTOS_REPETIDOS,
             ("2026-10-09", "egreso", BILLETERA_B, 82458.0, "Transferencia al banco (cuenta propia)")),
        "2026-10-01", "2026-10-09")
    assert [f[3] for f in r["sin_pareja"]] == [82458.0]


def test_c_mismo_contenedor_no_vale():
    r = verificar_transferencias_propias(_con(
        ("2026-10-07", "egreso", BANCO, 150000.0, f"TRANSF.HB.E / {CUIT}-JUAN"),
        ("2026-10-07", "ingreso", BANCO, 150000.0, "Credito / 99222222223-GOMEZ")), "2026-10-01", "2026-10-09")
    assert len(r["sin_pareja"]) == 1 and r["pares"] == []
