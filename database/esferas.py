"""
PWOS -- clasificacion de esfera economica de un movimiento (Epica 10).

`movimientos.tipo` ya distingue esto desde el dia uno del schema -- no
hace falta una columna nueva ni una migracion sobre los movimientos ya
cargados. Este modulo centraliza el mapeo tipo -> esfera (mismo patron
que valorizacion.py) para que los modulos nuevos de la app (Movimientos
y gastos, Renta Fija, Renta Variable, etc.) no lo reimplementen cada uno
a su manera.

Eje aparte, no confundir: `categorias.tipo` ('ingreso'/'egreso'/'ambos')
sirve solo para agrupar subcategorias en la UI (Transporte, Supermercado)
-- no decide esfera economica. Ver auditoria del arbol de categorias
sugerido (schema-base-datos.md) para el detalle de por que "Inversiones"
no deberia vivir bajo EGRESOS ahi.
"""

ESFERA_DE_TIPO = {
    "ingreso": "consumo",
    "egreso": "consumo",
    "transferencia": "rebalanceo",
    "compra_activo": "inversion",
    "venta_activo": "inversion",
    "renta": "renta_pasiva",
}


def esfera_de(tipo_movimiento: str) -> str:
    """'consumo' / 'rebalanceo' / 'inversion' / 'renta_pasiva'.

    KeyError intencional si aparece un tipo no contemplado -- mejor
    romper visible que clasificar a ciegas."""
    return ESFERA_DE_TIPO[tipo_movimiento]


def tipos_de_esfera(esfera: str) -> list[str]:
    """Inverso de esfera_de(): los `movimientos.tipo` que caen en una
    esfera dada (ej. 'consumo' -> ['ingreso', 'egreso']). Para que los
    modulos que arman un WHERE tipo IN (...) por esfera (ej. el filtro
    de "Movimientos y gastos") no hardcodeen la lista aparte."""
    return [tipo for tipo, e in ESFERA_DE_TIPO.items() if e == esfera]
