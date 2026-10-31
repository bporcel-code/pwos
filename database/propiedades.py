"""
PWOS -- CRUD de `propiedades` (Real Estate, 2026-09-17, pedido del
usuario: cerrar la brecha de flujo/valuacion que hoy no existia). Catalogo
chico, sin filtros server-side (mismo criterio que tesis_bonos.py) --
se trae todo y se filtra en Python/JS si hace falta.

Cada propiedad NO tiene contenedor propio -- decision ya tomada (ver
ESTADO_PWOS.md, Real Estate Fase 2): el alquiler/flujo real vive en un
contenedor ya existente (ej. una billetera), esta tabla es solo el
catalogo/metadata de la propiedad en si (a que % le corresponde, desde
cuando, notas de catastro).
"""

VALORES_TIPO = ("activo_fisico", "fondo_re")


def _normalizar_alias(alias: str) -> str:
    return alias.strip().upper()


def crear_propiedad(con, *, alias: str, tipo: str, pct_propiedad: float, fecha_alta: str,
                     fecha_disponibilidad_capital: str | None = None, notas: str | None = None) -> int:
    if tipo not in VALORES_TIPO:
        raise ValueError(f"tipo invalido: {tipo!r} (valores validos: {VALORES_TIPO})")
    if not (0 < pct_propiedad <= 100):
        raise ValueError(f"pct_propiedad invalido: {pct_propiedad!r} (debe ser > 0 y <= 100)")
    if fecha_disponibilidad_capital is not None and tipo != "fondo_re":
        raise ValueError("fecha_disponibilidad_capital solo aplica a tipo='fondo_re'")
    cur = con.execute(
        """
        INSERT INTO propiedades (alias, tipo, pct_propiedad, fecha_alta, fecha_disponibilidad_capital, notas)
        VALUES (?, ?, ?, ?, ?, ?);
        """,
        (_normalizar_alias(alias), tipo, pct_propiedad, fecha_alta, fecha_disponibilidad_capital, notas),
    )
    con.commit()
    return cur.lastrowid


def obtener_propiedad_id(con, alias: str) -> int | None:
    row = con.execute("SELECT id FROM propiedades WHERE alias = ?;", (_normalizar_alias(alias),)).fetchone()
    return row[0] if row else None


def listar_propiedades(con) -> list[dict]:
    filas = con.execute(
        """
        SELECT id, alias, tipo, pct_propiedad, fecha_alta, fecha_disponibilidad_capital, notas,
               creado_en, actualizado_en, inversion_inicial_usd
        FROM propiedades ORDER BY alias;
        """
    ).fetchall()
    return [
        {
            "id": r[0], "alias": r[1], "tipo": r[2], "pct_propiedad": r[3], "fecha_alta": r[4],
            "fecha_disponibilidad_capital": r[5], "notas": r[6], "creado_en": r[7], "actualizado_en": r[8],
            "inversion_inicial_usd": r[9],
        }
        for r in filas
    ]


def actualizar_inversion_inicial(con, propiedad_id: int, inversion_inicial_usd: float | None) -> None:
    """Capital propio puesto para adquirir/construir la propiedad (Real
    Estate -- inversion inicial + TIR/VPN, 2026-09-19).
    None a proposito para una propiedad 100% financiada por un pasivo
    -- no se inventa un 0. `fecha_alta` (ya existente)
    se reusa como fecha de este flujo en flujo_inmueble.py, sin columna
    de fecha nueva.

    CONVENCION (fix 2026-09-19: una primera carga la violo sin querer,
    cargo el costo total de obra en vez de la parte propia):
    `inversion_inicial_usd` es SIEMPRE la porcion NETA que
    puso el usuario, nunca el costo total de la propiedad -- MISMA
    convencion que `alquileres.monto`/`pasivos.monto_original_usd`. Si
    la propiedad es co-poseida, el valor pasado aca ya tiene que venir
    dividido por `pct_propiedad`; esta funcion NO aplica ningun % ni lo
    valida, es responsabilidad de quien llama (mismo criterio que
    alquileres.py/pasivos.py)."""
    if inversion_inicial_usd is not None and inversion_inicial_usd < 0:
        raise ValueError(f"inversion_inicial_usd invalido: {inversion_inicial_usd!r} (debe ser >= 0 o None)")
    con.execute(
        "UPDATE propiedades SET inversion_inicial_usd = ?, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?;",
        (inversion_inicial_usd, propiedad_id),
    )
    con.commit()
