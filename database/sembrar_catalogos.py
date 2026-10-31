"""PWOS -- siembra de catalogos en una base nueva (paso 7a2 de la publicacion).

Una base nueva sale de create_schema.py + create_views.py sin ninguna fila.
Esto carga lo que el motor necesita y que no es de ningun usuario:
- categorias genericas: las que el codigo busca por nombre (costos de
  operatoria, impuestos, ajustes, movimientos neutros, alquileres) y unas de
  consumo para empezar (sueldo, hogar, servicios);
- parametros_rv: los umbrales por defecto de la solapa Analisis - Acciones;
- indicadores_catalogo: catalogos/indicadores_catalogo.json (sin umbrales ni
  notas: indicadores_anotaciones es de cada usuario).

Re-ejecutable: solo inserta lo que falta, nunca pisa una fila existente.

Uso (parado en database/):
    python sembrar_catalogos.py --db <base nueva>
"""
import argparse
import json
import sqlite3
from pathlib import Path

CATALOGO_INDICADORES = Path(__file__).parent / "catalogos" / "indicadores_catalogo.json"

# (nombre, padre, tipo)
CATEGORIAS = [
    ("Costos de operatoria financiera", None, "egreso"),
    ("Comisiones de caución", "Costos de operatoria financiera", "egreso"),
    ("Comisiones de compra/venta", "Costos de operatoria financiera", "egreso"),
    ("Impuestos sobre inversiones", None, "egreso"),
    ("Ajustes contables", None, "egreso"),
    ("Movimientos neutros", None, "neutro"),
    ("Alquileres", None, "ingreso"),
    ("Sueldo", None, "ingreso"),
    ("Gastos del hogar", None, "egreso"),
    ("Servicios", None, "egreso"),
]

# (clave, valor, descripcion)
PARAMETROS_RV = [
    ("tope_posicion_rv_pct", 10.0, "Tope de una posicion como % de la renta variable (borrador)."),
    ("tope_posicion_patrimonio_pct", 5.0, "Tope de una posicion como % del patrimonio bruto (borrador)."),
    ("tope_sector_rv_pct", 25.0, "Tope de un sector como % de la renta variable (borrador)."),
    ("banda_rebalanceo_pp", 3.0, "Banda de rebalanceo en puntos porcentuales."),
    ("objetivo_cubeta_valor_min", 30.0, "Cubeta Valor Graham puro: minimo objetivo, % de la RV."),
    ("objetivo_cubeta_valor_max", 40.0, "Cubeta Valor Graham puro: maximo objetivo, % de la RV."),
    ("objetivo_cubeta_calidad_min", 45.0, "Cubeta Calidad a precio razonable: minimo objetivo, % de la RV."),
    ("objetivo_cubeta_calidad_max", 55.0, "Cubeta Calidad a precio razonable: maximo objetivo, % de la RV."),
    ("objetivo_cubeta_riesgo_max", 15.0, "Cubeta Mayor riesgo: maximo, % de la RV."),
    ("peso_objetivo_rv_patrimonio_pct", None,
     "Peso objetivo de la renta variable sobre el patrimonio bruto. NULL hasta que el usuario lo defina."),
    ("umbral_cedear_solo_usd_pct", 1.2,
     "CEDEAR en pesos o dolares, caso 'tengo solo dolares': spread minimo (%) para que convenga vender USD "
     "via MEP y comprar en pesos (costo de la vuelta por MEP)."),
    ("desvio_cedear_alerta_pct", 2.0,
     "CEDEAR caro/barato: desvio (%) del CCL implicito contra la mediana del mercado a partir del cual se "
     "marca el badge 'barato'/'caro'."),
]


def sembrar(con: sqlite3.Connection) -> dict:
    n = {"categorias": 0, "parametros_rv": 0, "indicadores_catalogo": 0}
    for nombre, padre, tipo in CATEGORIAS:
        if con.execute("SELECT 1 FROM categorias WHERE nombre = ?;", (nombre,)).fetchone():
            continue
        padre_id = None
        if padre:
            padre_id = con.execute("SELECT id FROM categorias WHERE nombre = ?;", (padre,)).fetchone()[0]
        con.execute("INSERT INTO categorias (nombre, categoria_padre_id, tipo) VALUES (?, ?, ?);",
                    (nombre, padre_id, tipo))
        n["categorias"] += 1
    for clave, valor, descripcion in PARAMETROS_RV:
        n["parametros_rv"] += con.execute(
            "INSERT OR IGNORE INTO parametros_rv (clave, valor, descripcion) VALUES (?, ?, ?);",
            (clave, valor, descripcion)).rowcount
    for fila in json.loads(CATALOGO_INDICADORES.read_text(encoding="utf-8")):
        columnas = ", ".join(fila)
        marcas = ", ".join("?" * len(fila))
        n["indicadores_catalogo"] += con.execute(
            f"INSERT OR IGNORE INTO indicadores_catalogo ({columnas}) VALUES ({marcas});",
            list(fila.values())).rowcount
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, required=True, help="base nueva (create_schema.py + create_views.py)")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)
    with con:
        print(sembrar(con))
    con.close()
