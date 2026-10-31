"""
Tests de cierres_mensuales.py (carga incremental de la solapa CUENTAS) y de
la fecha de cada cierre en rendimiento.serie_rendimiento_mensual.

Todo sintetico: un Excel armado con openpyxl con la misma forma que CUENTAS
(fila MESES, encabezado por mes cada 2 columnas, filas SUB TOTAL [USD] /
TRABAJO / GASTOS / MEP) y una base de prueba de create_schema/create_views.
No toca V: ni el sheet real.

Uso (parado en database/):
    python -m pytest -q test_cierres_mensuales.py
"""
import sqlite3
from datetime import datetime

import openpyxl
import pytest

import cierres_mensuales as cm
import rendimiento
from create_schema import create_schema
from create_views import create_views


def armar_xlsx(ruta, meses, extra_encabezados=()):
    """meses: [(encabezado, sub_total_usd, trabajo, gastos, mep)]; None = celda vacia."""
    wb = openpyxl.Workbook()
    wb.active.title = "TABLERO"
    ws = wb.create_sheet("CUENTAS")
    ws["B6"] = "MESES"
    filas = {"SUB TOTAL [USD]": 27, "TRABAJO": 28, "GASTOS": 29, "MEP": 24}
    for etiqueta, fila in filas.items():
        ws.cell(row=fila, column=2, value=etiqueta)
    ws.cell(row=7, column=2, value="UBICACIÓN")
    for i, (enc, patr, trab, gast, mep) in enumerate(list(meses) + list(extra_encabezados)):
        col = 3 + 2 * i
        ws.cell(row=6, column=col, value=enc)
        for fila, valor in ((27, patr), (28, trab), (29, gast), (24, mep)):
            if valor is not None:
                ws.cell(row=fila, column=col, value=valor)
    wb.save(ruta)
    return ruta


@pytest.fixture
def db(tmp_path):
    ruta = tmp_path / "prueba.db"
    create_schema(ruta)
    create_views(ruta)
    return ruta


MESES_OK = [
    (datetime(2026, 7, 26), 1000.00, 150_000, 50_000, 1000.0),
    (datetime(2026, 8, 26), 1120.00, 160_000, 60_000, 1000.0),  # aporte 100 -> rendimiento 20
    (datetime(2026, 9, 26), 1000.00, 170_000, 70_000, 1000.0),  # aporte 100 - 300 de deuda -> rendimiento 80
]
SIN_CARGAR = [(datetime(2026, 10, 26), "#DIV/0!", None, None, None)]


def _pago_deuda(ruta, fecha, monto):
    con = sqlite3.connect(ruta)
    pid = con.execute("INSERT OR IGNORE INTO pasivos (alias, monto_original_usd, fecha_alta) "
                      "VALUES ('DEUDA', 1000, '2026-01-01');").lastrowid or 1
    con.execute("INSERT INTO pagos_pasivo (pasivo_id, fecha, monto_usd) VALUES (?, ?, ?);", (pid, fecha, monto))
    con.commit()
    con.close()


def test_lee_meses_cargados_y_saltea_los_vacios(tmp_path):
    x = armar_xlsx(tmp_path / "cierres_prueba.xlsx", MESES_OK, SIN_CARGAR)
    meses = cm.leer_cuentas(x)
    assert [(m["anio"], m["mes"]) for m in meses] == [(2026, 7), (2026, 8), (2026, 9)]
    assert meses[1]["patrimonio_usd"] == 1120.0 and meses[1]["mep"] == 1000.0


def test_encabezado_como_texto(tmp_path):
    x = armar_xlsx(tmp_path / "a.xlsx", [("ago - 24", 500.0, 1000, 0, 1000.0), ("sep - 24", 510.0, 1000, 0, 1000.0)])
    assert [(m["anio"], m["mes"]) for m in cm.leer_cuentas(x)] == [(2024, 8), (2024, 9)]


def test_mes_a_medio_cargar_frena(tmp_path):
    x = armar_xlsx(tmp_path / "a.xlsx", MESES_OK + [(datetime(2026, 10, 26), 1010.0, 100_000, None, 1000.0)])
    with pytest.raises(cm.CierresError, match="medio cargar"):
        cm.leer_cuentas(x)


def test_hueco_entre_meses_frena(tmp_path):
    x = armar_xlsx(tmp_path / "a.xlsx", [MESES_OK[0], MESES_OK[2]])
    with pytest.raises(cm.CierresError, match="hueco"):
        cm.leer_cuentas(x)


def test_pago_de_deuda_es_retiro(tmp_path, db):
    _pago_deuda(db, "2026-09-23", 300.0)
    x = armar_xlsx(tmp_path / "a.xlsx", MESES_OK)
    con = sqlite3.connect(db)
    c = cm.calcular(con, cm.leer_cuentas(x))
    assert [m["aportes_netos_usd"] for m in c] == [100.0, 100.0, -200.0]
    assert [m["rendimiento_usd"] for m in c] == [0.0, 20.0, 80.0]
    assert c[2]["retiros_deuda_usd"] == 300.0


def test_carga_solo_lo_nuevo_y_es_idempotente(tmp_path, db):
    x = armar_xlsx(tmp_path / "a.xlsx", MESES_OK[:2])
    r = cm.revisar(x, db, escribir=True)
    assert r["insertados"] == 2 and r["backup"]
    x = armar_xlsx(tmp_path / "a.xlsx", MESES_OK)
    seco = cm.revisar(x, db)
    assert seco["iguales"] == 2 and [(n["anio"], n["mes"]) for n in seco["nuevos"]] == [(2026, 9)]
    assert not seco["aplicado"]
    assert cm.revisar(x, db, escribir=True)["insertados"] == 1
    assert cm.revisar(x, db, escribir=True)["insertados"] == 0  # re-correr no duplica
    detalle = sqlite3.connect(db).execute("SELECT detalle_json FROM cierres_mensuales WHERE mes = 9;").fetchone()[0]
    assert '"mep": 1000.0' in detalle


def test_mes_ya_cargado_que_cambio_frena_sin_escribir(tmp_path, db):
    cm.revisar(armar_xlsx(tmp_path / "a.xlsx", MESES_OK[:2]), db, escribir=True)
    corregido = [MESES_OK[0], (datetime(2026, 8, 26), 1150.00, 160_000, 60_000, 1000.0), MESES_OK[2]]
    x = armar_xlsx(tmp_path / "a.xlsx", corregido)
    assert [(d["anio"], d["mes"]) for d in cm.revisar(x, db)["distintos"]] == [(2026, 8)]
    with pytest.raises(cm.CierresError, match="distinto"):
        cm.revisar(x, db, escribir=True)
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM cierres_mensuales;").fetchone()[0] == 2


def test_cada_cierre_se_dibuja_el_1_del_mes_siguiente(tmp_path, db):
    x = armar_xlsx(tmp_path / "a.xlsx", [(datetime(2025, 11, 25), 1000.0, 0, 0, 1000.0),
                                         (datetime(2025, 12, 25), 1010.0, 0, 0, 1000.0),
                                         (datetime(2026, 1, 26), 1030.0, 0, 0, 1000.0)])
    cm.revisar(x, db, escribir=True)
    tramos = rendimiento.serie_rendimiento_mensual(sqlite3.connect(db))
    assert [(t["fecha_inicio"], t["fecha_fin"]) for t in tramos] == [("2025-12-01", "2026-01-01"),
                                                                    ("2026-01-01", "2026-02-01")]
    assert tramos[0]["rendimiento_pct"] == pytest.approx(1.0)
