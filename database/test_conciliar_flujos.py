"""
Tests de conciliar_flujos.py (Tanda 3), todos sobre bases de PRUEBA
(create_schema + create_views + sembrar_catalogos) con datos ficticios; no
tocan ninguna base real, asi que una ronda nueva no los puede romper.

- `con`: un broker con un bono (cupon por cronograma) y un CEDEAR (dividendo
  proyectado). Los estimados se generan "como si hoy fuera 01/09/2026", sin el
  filtro de filas del importador, para simular la proxima ronda: el estimado
  existe ANTES de que se cargue el pago.
- `con_prueba`: un banco con dos contratos de alquiler (depto el 15, casa el 26,
  ARS 200.000 desde 2026-10). La "ronda" la simula cada test.

Uso:  python -m pytest test_conciliar_flujos.py -q
"""
import sqlite3
import sys
from datetime import date

import pytest

import conciliar_flujos as cf
import flujo_estimado as fe
from create_schema import create_schema
from create_views import create_views
from sembrar_catalogos import sembrar

BANCO_ARS = None  # cuenta ARS disponible del banco de `con_prueba` (la fija el fixture)
BONO, CEDEAR = "BX30", "CDXY"
DEPTO, CASA = "DEPTO-PRUEBA", "CASA-PRUEBA"


def _base(tmp_path):
    db = tmp_path / "prueba.db"
    create_schema(db)
    create_views(db)
    c = sqlite3.connect(db)
    sembrar(c)
    for ticker, precio in (("USD_MEP", 1500.0), ("USD_A3500", 1450.0)):
        aid = c.execute("INSERT INTO activos (ticker, tipo, moneda_nominal) VALUES (?, 'fx', 'USD');",
                        (ticker,)).lastrowid
        c.execute("INSERT INTO precios (activo_id, fecha, precio, moneda, fuente) VALUES (?, '2026-08-31', ?, 'ARS', 'prueba');",
                  (aid, precio))
    return c


def _compra(c, cuenta, contenedor, activo, fecha, cantidad, precio):
    mov = c.execute("INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, monto, moneda, descripcion) "
                    "VALUES (?, 'compra_activo', ?, ?, 'USD', 'compra');", (fecha, cuenta, cantidad * precio)).lastrowid
    c.execute("INSERT INTO operaciones_activos (movimiento_id, activo_id, contenedor_id, cantidad, precio_unitario, moneda) "
              "VALUES (?, ?, ?, ?, ?, 'USD');", (mov, activo, contenedor, cantidad, precio))


def _renta(c, cuenta, fecha, monto, desc):
    return c.execute("INSERT INTO movimientos (fecha, tipo, cuenta_destino_id, monto, moneda, descripcion) "
                     "VALUES (?, 'renta', ?, ?, 'USD', ?);", (fecha, cuenta, monto, desc)).lastrowid


@pytest.fixture
def con(tmp_path, monkeypatch):
    c = _base(tmp_path)
    broker = c.execute("INSERT INTO contenedores (nombre, tipo) VALUES ('BROKER PRUEBA', 'broker');").lastrowid
    usd = c.execute("INSERT INTO cuentas (contenedor_id, moneda, estado) VALUES (?, 'USD', 'disponible');",
                    (broker,)).lastrowid
    # Bono: 1.000 VN, cupon de 2 por VN100 el 30/09 -> USD 20.
    bono = c.execute("INSERT INTO activos (ticker, tipo, moneda_nominal) VALUES (?, 'bono_soberano', 'USD');",
                     (BONO,)).lastrowid
    c.execute("INSERT INTO bonos_referencia (activo_id, moneda_pago) VALUES (?, 'USD');", (bono,))
    c.execute("INSERT INTO cronograma_pagos_bonos (activo_id, fecha_pago, tipo_pago, monto_por_vn) "
              "VALUES (?, '2026-09-30', 'interes', 2.0);", (bono,))
    _compra(c, usd, broker, bono, "2026-01-10", 1000, 0.6)
    # CEDEAR: 10 CEDEARs, ratio 5, dividendo de USD 0,50 por accion el 03/10.
    cedear = c.execute("INSERT INTO activos (ticker, tipo, moneda_nominal) VALUES (?, 'cedear', 'USD');",
                       (CEDEAR,)).lastrowid
    c.execute("INSERT INTO ratios_cedear (ticker_byma, ticker_origen, ratio_cedear, ratio_subyacente, ratio, pais, "
              "fuente_ratio, fecha_lista) VALUES (?, ?, 5, 1, 5, 'Estados Unidos', 'prueba', '2026-09-01');",
              (CEDEAR, CEDEAR))
    c.execute("INSERT INTO dividendos_proyectados (activo_id, simbolo, fecha_ex, fecha_pago, monto_por_accion, moneda, "
              "tipo, actualizado_en) VALUES (?, ?, '2026-09-25', '2026-10-03', 0.5, 'USD', 'anunciado', '2026-09-01');",
              (cedear, CEDEAR))
    _compra(c, usd, broker, cedear, "2026-01-10", 10, 20.0)
    # Lo que trae la ronda: el cupon con su ticker (y la fila 'importador' que deja
    # el importador de ese broker) y el dividendo SIN ticker en el texto.
    mov_bono = _renta(c, usd, "2026-10-01", 20.0, f"Pago de Renta({BONO} US$)")
    c.execute("INSERT INTO flujos_esperados (activo_id, tipo_flujo, fecha_estimada, monto_estimado, moneda, "
              "origen_estimado, estado, movimiento_id) VALUES (?, 'cupon', '2026-09-30', 20.0, 'USD', 'importador', "
              "'cobrado', ?);", (bono, mov_bono))
    dividendo = round(0.5 * 10 / 5 * fe.FACTOR_NETO_CEDEAR_EEUU, 2)
    _renta(c, usd, "2026-10-06", dividendo, "Nota De Credito Conversion")
    c.commit()
    monkeypatch.setattr(fe, "_ya_cobrado_por_importador", lambda con, e: False)
    fe.sincronizar(c, date(2026, 9, 1))
    yield c
    c.close()


@pytest.fixture
def con_prueba(tmp_path, monkeypatch):
    c = _base(tmp_path)
    banco = c.execute("INSERT INTO contenedores (nombre, tipo) VALUES ('BANCO PRUEBA', 'banco');").lastrowid
    cuenta = c.execute("INSERT INTO cuentas (contenedor_id, moneda, estado) VALUES (?, 'ARS', 'disponible');",
                       (banco,)).lastrowid
    monkeypatch.setattr(sys.modules[__name__], "BANCO_ARS", cuenta)
    # Lo que no es alquiler: los textos de prueba, no los de la configuracion del usuario.
    monkeypatch.setattr(cf, "EXCLUIR_ALQUILER", ("haberes", "titular prueba"))
    for alias, dia in ((DEPTO, 15), (CASA, 26)):
        c.execute("""INSERT INTO propiedades (alias, tipo, pct_propiedad, fecha_alta, alquiler_monto, alquiler_moneda,
                                             alquiler_dia_cobro, alquiler_contenedor_id, alquiler_desde)
                     VALUES (?, 'activo_fisico', 50, '2025-01-01', 200000, 'ARS', ?, ?, '2026-10-01');""",
                  (alias, dia, banco))
    c.commit()
    fe.sincronizar(c, date(2026, 9, 1))
    yield c
    c.close()


def _ingreso_banco(con, fecha, monto, desc):
    cur = con.execute(
        "INSERT INTO movimientos (fecha, tipo, cuenta_destino_id, monto, moneda, descripcion, naturaleza) "
        "VALUES (?, 'ingreso', ?, ?, 'ARS', ?, 'efectivo');", (fecha, BANCO_ARS, monto, desc))
    con.commit()
    return cur.lastrowid


def _flujo(con, concepto, fecha, contenedor=None):
    sql = """SELECT f.id, f.estado, f.movimiento_id FROM flujos_esperados f
             LEFT JOIN activos a ON a.id = f.activo_id LEFT JOIN propiedades p ON p.id = f.propiedad_id
             LEFT JOIN contenedores co ON co.id = f.contenedor_id
             WHERE COALESCE(a.ticker, p.alias) = ? AND f.fecha_estimada = ? AND f.origen_estimado <> 'importador'"""
    params = [concepto, fecha]
    if contenedor:
        sql += " AND co.nombre = ?"
        params.append(contenedor)
    return con.execute(sql, params).fetchone()


def test_bono_atribuido_y_borra_fila_del_importador(con):
    cf.conciliar(con, date(2026, 10, 5))
    _, estado, mov = _flujo(con, BONO, "2026-09-30")
    assert estado == "cobrado"
    assert BONO in con.execute("SELECT descripcion FROM movimientos WHERE id = ?;", (mov,)).fetchone()[0]
    # la fila 'importador' para el mismo movimiento ya no existe
    assert con.execute("SELECT COUNT(*) FROM flujos_esperados WHERE origen_estimado = 'importador' AND movimiento_id = ?;",
                       (mov,)).fetchone()[0] == 0


def test_dividendo_sin_ticker_por_monto(con):
    cf.conciliar(con, date(2026, 10, 10))
    _, estado, mov = _flujo(con, CEDEAR, "2026-10-03", "BROKER PRUEBA")
    assert estado == "cobrado"
    desc, monto = con.execute("SELECT descripcion, monto FROM movimientos WHERE id = ?;", (mov,)).fetchone()
    assert desc == "Nota De Credito Conversion" and monto == pytest.approx(0.64)


def test_alquiler_claro_carga_alquileres(con_prueba):
    con = con_prueba
    mov = _ingreso_banco(con, "2026-10-16", 201500.0, "Credito Transferencia / 99000000001-INQUILINO PRUEBA-VAR")
    _ingreso_banco(con, "2026-10-16", 1000000.0, "TRANSF / 99000000002-TITULAR PRUEBA")  # propio: excluido
    cf.conciliar(con, date(2026, 10, 20))
    assert _flujo(con, DEPTO, "2026-10-15")[1:] == ("cobrado", mov)
    fila = con.execute("SELECT monto, fecha_cobro FROM alquileres a JOIN propiedades p ON p.id = a.propiedad_id "
                       "WHERE p.alias = ? AND periodo = '2026-10';", (DEPTO,)).fetchone()
    assert fila == (201500.0, "2026-10-16")
    # desvincular revierte las dos cosas
    cf.desvincular(con, _flujo(con, DEPTO, "2026-10-15")[0])
    assert _flujo(con, DEPTO, "2026-10-15")[1] == "pendiente"
    assert con.execute("SELECT COUNT(*) FROM alquileres WHERE periodo = '2026-10';").fetchone()[0] == 0


def test_alquiler_empatado_no_adivina(con_prueba):
    con = con_prueba
    m1 = _ingreso_banco(con, "2026-10-21", 200000.0, "CREDIN - TRANSFERENCIA / INMOBILIARIA X")
    m2 = _ingreso_banco(con, "2026-10-22", 200000.0, "CREDIN - TRANSFERENCIA / INMOBILIARIA X")
    r = cf.conciliar(con, date(2026, 11, 2))
    casa_id, estado, _ = _flujo(con, CASA, "2026-10-26")
    assert estado == "pendiente"
    assert {m["id"] for m in r["ambiguos"][casa_id]} >= {m1, m2}
    cf.vincular_manual(con, casa_id, m2)
    assert _flujo(con, CASA, "2026-10-26")[1:] == ("cobrado", m2)
    with pytest.raises(ValueError):
        cf.vincular_manual(con, casa_id, m1)  # ya cobrado


def test_no_cobrado_solo_si_la_ronda_cubre_la_fecha(con_prueba):
    con = con_prueba
    # Sin movimientos del banco posteriores a la ventana: sigue pendiente.
    cf.conciliar(con, date(2026, 11, 2))
    assert _flujo(con, DEPTO, "2026-10-15")[1] == "pendiente"
    # La ronda carga el banco hasta el 31/10 (un gasto) y el alquiler no aparecio.
    con.execute("INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, monto, moneda, descripcion, naturaleza) "
                "VALUES ('2026-10-31', 'egreso', ?, 1000, 'ARS', 'gasto', 'efectivo');", (BANCO_ARS,))
    con.commit()
    cf.conciliar(con, date(2026, 11, 2))
    assert _flujo(con, DEPTO, "2026-10-15")[1] == "no_cobrado"
    # Pago tardio (19/10: dentro de la ventana del depto, antes de la de la casa): se vincula igual.
    # Un 200.000 el 24/10 iria a la casa (dia 26, mas cerca): entre los dos alquileres decide la fecha.
    mov = _ingreso_banco(con, "2026-10-19", 200000.0, "Transferencia inquilino")
    cf.conciliar(con, date(2026, 11, 2))
    assert _flujo(con, DEPTO, "2026-10-15")[1:] == ("cobrado", mov)


def test_flujo_cobros_compara_estimado_y_real(con):
    cf.conciliar(con, date(2026, 10, 10))
    r = fe.flujo_cobros(con, date(2026, 10, 10))
    octubre = next(m for m in r["meses"] if m["mes"] == "2026-10")
    assert octubre["estimado_cobrado_usd"] > 0
    assert octubre["diferencia_usd"] == pytest.approx(octubre["real_cobrado_usd"] - octubre["estimado_cobrado_usd"], abs=0.02)
    assert r["meses"][0]["mes"] == "2026-10"  # nada antes del inicio del seguimiento
