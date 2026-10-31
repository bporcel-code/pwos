"""
Tests de flujo_estimado.py sobre una base de PRUEBA (create_schema +
create_views + sembrar_catalogos): un banco, un departamento con contrato de
alquiler y el MEP/A3500. Datos ficticios; no toca ninguna base real.

Ciclo de vida que se prueba:
  A. sincronizar dos veces seguidas: la segunda no escribe nada.
  B. cambia el contrato de alquiler: se actualizan solo los estimados con
     fecha FUTURA; el de un cobro ya vencido (a confirmar) queda fijo.
  C. se saca el contrato: se borran los futuros, el vencido queda.
  D. un cobrado nunca se toca.

Uso:  python -m pytest test_flujo_estimado.py -q
"""
import sqlite3
from datetime import date

import pytest

import flujo_estimado as fe
from create_schema import create_schema
from create_views import create_views
from sembrar_catalogos import sembrar

ALIAS = "DEPTO-PRUEBA"


@pytest.fixture
def con(tmp_path):
    db = tmp_path / "prueba.db"
    create_schema(db)
    create_views(db)
    c = sqlite3.connect(db)
    sembrar(c)
    banco = c.execute("INSERT INTO contenedores (nombre, tipo) VALUES ('BANCO PRUEBA', 'banco');").lastrowid
    c.execute("INSERT INTO cuentas (contenedor_id, moneda, estado) VALUES (?, 'ARS', 'disponible');", (banco,))
    for ticker, precio in (("USD_MEP", 1500.0), ("USD_A3500", 1450.0)):
        aid = c.execute("INSERT INTO activos (ticker, tipo, moneda_nominal) VALUES (?, 'fx', 'USD');", (ticker,)).lastrowid
        c.execute("INSERT INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, '2026-09-30', ?, 'ARS', 'prueba');",
                  (aid, precio))
    c.execute("""INSERT INTO propiedades (alias, tipo, pct_propiedad, fecha_alta, alquiler_monto, alquiler_moneda,
                                         alquiler_dia_cobro, alquiler_contenedor_id, alquiler_desde)
                 VALUES (?, 'activo_fisico', 100, '2025-01-01', 200000, 'ARS', 15, ?, '2026-10-01');""", (ALIAS, banco))
    c.commit()
    yield c
    c.close()


def _alquileres(c):
    return dict(c.execute(
        """SELECT f.fecha_estimada, f.monto_estimado FROM flujos_esperados f
           JOIN propiedades p ON p.id = f.propiedad_id WHERE p.alias = ?;""", (ALIAS,)
    ).fetchall())


def test_a_idempotente(con):
    primera = fe.sincronizar(con, date(2026, 10, 5))
    assert primera["insertar"] > 0
    assert fe.sincronizar(con, date(2026, 10, 5)) == {"insertar": 0, "actualizar": 0, "borrar": 0}
    assert _alquileres(con)["2026-10-15"] == 200000


def test_b_cambio_de_contrato_no_toca_lo_vencido(con):
    fe.sincronizar(con, date(2026, 10, 5))
    con.execute("UPDATE propiedades SET alquiler_monto = 230000 WHERE alias = ?;", (ALIAS,))
    # "hoy" = 20/10: el cobro del 15/10 ya vencio (a confirmar) -> queda en 200.000
    conteo = fe.sincronizar(con, date(2026, 10, 20))
    alq = _alquileres(con)
    assert alq["2026-10-15"] == 200000
    assert alq["2026-11-15"] == 230000
    assert conteo["actualizar"] >= 11


def test_c_sin_contrato_borra_solo_futuros(con):
    fe.sincronizar(con, date(2026, 10, 5))
    con.execute("UPDATE propiedades SET alquiler_monto = NULL WHERE alias = ?;", (ALIAS,))
    fe.sincronizar(con, date(2026, 10, 20))
    assert _alquileres(con) == {"2026-10-15": 200000}


def test_d_cobrado_no_se_toca(con):
    fe.sincronizar(con, date(2026, 10, 5))
    con.execute(
        """UPDATE flujos_esperados SET estado = 'cobrado' WHERE fecha_estimada = '2026-11-15'
           AND propiedad_id = (SELECT id FROM propiedades WHERE alias = ?);""", (ALIAS,)
    )
    con.execute("UPDATE propiedades SET alquiler_monto = NULL WHERE alias = ?;", (ALIAS,))
    fe.sincronizar(con, date(2026, 10, 5))
    # el 15/10 (futuro, pendiente) se borra; el 15/11 (cobrado) queda aunque sea futuro
    assert _alquileres(con) == {"2026-11-15": 200000}


def test_flujo_cobros_estados(con):
    fe.sincronizar(con, date(2026, 10, 5))
    r = fe.flujo_cobros(con, date(2026, 10, 20))
    estados = {(e["concepto"], e["fecha"]): e["estado"] for e in r["eventos"]}
    assert estados[(ALIAS, "2026-10-15")] == "a_confirmar"
    assert estados[(ALIAS, "2026-11-15")] == "estimado"
    octubre = r["meses"][0]
    assert octubre["mes"] == "2026-10"
    assert octubre["renta_usd"] + octubre["amortizacion_usd"] == pytest.approx(octubre["total_usd"], abs=0.02)
