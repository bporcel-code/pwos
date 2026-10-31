"""
PWOS -- Indicadores de tesis por posicion de Renta Variable ("que tiene
que seguir siendo verdad"), Epica 19 Fase 2 (v2.32, 2026-09-21).

Cada posicion tiene 3 a 5 indicadores (guia, no se fuerza) con un umbral
verde y uno rojo -- el amarillo es el tramo intermedio. El usuario carga a
mano el valor de cada lectura; el `estado` (verde/amarillo/rojo) se
calcula al guardarla.

  mayor_es_mejor (ej. ROIC):           valor >= verde -> verde
                                        valor <= rojo  -> rojo
  menor_es_mejor (ej. deuda/EBITDA):   valor <= verde -> verde
                                        valor >= rojo  -> rojo
  en el medio -> amarillo. Los umbrales son INCLUSIVOS del lado del
  verde y del rojo, y no pueden ser iguales entre si (lo obliga un CHECK
  de tabla y se valida aca antes para dar un mensaje claro).

Al editar umbrales/direccion de un indicador se RECALCULA el estado de
todo su historial: lo que queda como registro es el `valor` crudo, y un
color viejo calculado con umbrales ya corregidos engaña mas que ayuda.

Semaforo de la posicion = el peor estado entre sus indicadores ACTIVOS
que tienen al menos una lectura (usa la ULTIMA lectura de cada uno). Se
devuelve junto con la cobertura (`con_dato` de `total`) -- nunca se
muestra un "verde" completo si hay indicadores sin dato. Sin indicadores
activos con lectura -> `estado` None (gris).

NOTA de infraestructura: las conexiones de api.py NO activan
`PRAGMA foreign_keys`, asi que el ON DELETE CASCADE de la base no se
dispara en produccion (limitacion previa del proyecto, vale para todas
las tablas). Por eso los borrados de este modulo eliminan las lecturas
de forma EXPLICITA en la misma transaccion, y las altas validan la
existencia del padre en Python.
"""
import math
import sqlite3
from datetime import date
import configuracion

DIRECCIONES = ("mayor_es_mejor", "menor_es_mejor")
ESTADOS = ("verde", "amarillo", "rojo")
_SEVERIDAD = {"verde": 0, "amarillo": 1, "rojo": 2}
CAMPOS_EDITABLES = ("nombre", "fuente", "frecuencia", "direccion", "umbral_verde", "umbral_rojo", "activo")


def calcular_estado(direccion: str, umbral_verde: float, umbral_rojo: float, valor: float) -> str:
    if direccion == "mayor_es_mejor":
        if valor >= umbral_verde:
            return "verde"
        if valor <= umbral_rojo:
            return "rojo"
        return "amarillo"
    if direccion == "menor_es_mejor":
        if valor <= umbral_verde:
            return "verde"
        if valor >= umbral_rojo:
            return "rojo"
        return "amarillo"
    raise ValueError(f"direccion invalida: {direccion!r} (validas: {DIRECCIONES})")


def _validar_umbrales(direccion, umbral_verde, umbral_rojo) -> None:
    if direccion not in DIRECCIONES:
        raise ValueError(f"direccion invalida: {direccion!r} (validas: {DIRECCIONES})")
    for nombre, v in (("umbral_verde", umbral_verde), ("umbral_rojo", umbral_rojo)):
        if v is None or not math.isfinite(v):
            raise ValueError(f"{nombre} tiene que ser un numero")
    if direccion == "mayor_es_mejor" and not umbral_verde > umbral_rojo:
        raise ValueError("Con 'mayor es mejor' el umbral verde tiene que ser MAYOR que el rojo")
    if direccion == "menor_es_mejor" and not umbral_verde < umbral_rojo:
        raise ValueError("Con 'menor es mejor' el umbral verde tiene que ser MENOR que el rojo")


def _activo_id(con, ticker: str) -> int:
    fila = con.execute("SELECT id FROM activos WHERE ticker = ?;", (ticker,)).fetchone()
    if fila is None:
        raise LookupError(f"Ticker {ticker!r} no existe en 'activos'")
    return fila[0]


def _fila_indicador(con, indicador_id: int):
    fila = con.execute(
        "SELECT direccion, umbral_verde, umbral_rojo, nombre FROM tesis_indicadores WHERE id = ?;",
        (indicador_id,),
    ).fetchone()
    if fila is None:
        raise LookupError(f"No existe el indicador id={indicador_id}")
    return fila


def _nombre_valido(nombre) -> str:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("'nombre' no puede estar vacio")
    return nombre


def crear_indicador(con, *, ticker: str, nombre: str, direccion: str, umbral_verde: float,
                    umbral_rojo: float, fuente: str | None = None, frecuencia: str | None = None) -> int:
    activo_id = _activo_id(con, ticker)
    nombre = _nombre_valido(nombre)
    _validar_umbrales(direccion, umbral_verde, umbral_rojo)
    try:
        cur = con.execute(
            """
            INSERT INTO tesis_indicadores (activo_id, nombre, fuente, frecuencia, direccion, umbral_verde, umbral_rojo)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (activo_id, nombre, fuente, frecuencia, direccion, umbral_verde, umbral_rojo),
        )
    except sqlite3.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise ValueError(f"Ya existe un indicador '{nombre}' en {ticker}") from None
        raise
    con.commit()
    return cur.lastrowid


def actualizar_indicador(con, indicador_id: int, campos: dict) -> None:
    """UPDATE PARCIAL: solo se tocan las claves presentes en `campos`. Si
    cambia la direccion o algun umbral se recalcula el estado de todo el
    historial del indicador (ver docstring del modulo)."""
    desconocidos = set(campos) - set(CAMPOS_EDITABLES)
    if desconocidos:
        raise ValueError(f"campos no editables: {sorted(desconocidos)}")
    direccion, verde, rojo, nombre = _fila_indicador(con, indicador_id)

    nuevo_dir = campos.get("direccion", direccion)
    nuevo_verde = campos.get("umbral_verde", verde)
    nuevo_rojo = campos.get("umbral_rojo", rojo)
    _validar_umbrales(nuevo_dir, nuevo_verde, nuevo_rojo)
    if "nombre" in campos:
        campos = {**campos, "nombre": _nombre_valido(campos["nombre"])}
    if "activo" in campos:
        if campos["activo"] not in (0, 1, True, False):
            raise ValueError("'activo' tiene que ser 0 o 1")
        campos = {**campos, "activo": int(campos["activo"])}
    if not campos:
        return

    sets = ", ".join(f"{col} = ?" for col in campos)
    try:
        con.execute(
            f"UPDATE tesis_indicadores SET {sets}, actualizado_en = CURRENT_TIMESTAMP WHERE id = ?;",
            (*campos.values(), indicador_id),
        )
    except sqlite3.IntegrityError as e:
        con.rollback()
        if "UNIQUE" in str(e):
            raise ValueError(f"Ya existe un indicador '{campos.get('nombre')}' en esa posicion") from None
        raise

    if any(k in campos for k in ("direccion", "umbral_verde", "umbral_rojo")):
        lecturas = con.execute(
            "SELECT fecha, valor FROM tesis_indicadores_historial WHERE indicador_id = ?;", (indicador_id,)
        ).fetchall()
        for fecha, valor in lecturas:
            con.execute(
                "UPDATE tesis_indicadores_historial SET estado = ? WHERE indicador_id = ? AND fecha = ?;",
                (calcular_estado(nuevo_dir, nuevo_verde, nuevo_rojo, valor), indicador_id, fecha),
            )
    con.commit()


def eliminar_indicador(con, indicador_id: int) -> None:
    _fila_indicador(con, indicador_id)  # LookupError si no existe
    con.execute("DELETE FROM tesis_indicadores_historial WHERE indicador_id = ?;", (indicador_id,))
    con.execute("DELETE FROM tesis_indicadores WHERE id = ?;", (indicador_id,))
    con.commit()


def _fecha_lectura(fecha, hoy: date) -> str:
    try:
        d = fecha if isinstance(fecha, date) else date.fromisoformat(str(fecha))
    except ValueError:
        raise ValueError("fecha invalida (esperado AAAA-MM-DD)") from None
    if d > hoy:
        raise ValueError("la fecha de una lectura no puede ser futura")
    return d.isoformat()


def cargar_lectura(con, indicador_id: int, fecha, valor: float, hoy: date | None = None) -> str:
    """UPSERT de la lectura de (indicador, fecha): cargar la misma fecha
    corrige la anterior. Devuelve el estado calculado."""
    direccion, verde, rojo, _ = _fila_indicador(con, indicador_id)
    if valor is None or not math.isfinite(valor):
        raise ValueError("'valor' tiene que ser un numero")
    fecha_iso = _fecha_lectura(fecha, hoy or configuracion.hoy())
    estado = calcular_estado(direccion, verde, rojo, valor)
    con.execute(
        """
        INSERT INTO tesis_indicadores_historial (indicador_id, fecha, valor, estado)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (indicador_id, fecha) DO UPDATE SET valor = excluded.valor, estado = excluded.estado;
        """,
        (indicador_id, fecha_iso, valor, estado),
    )
    con.commit()
    return estado


def eliminar_lectura(con, indicador_id: int, fecha) -> None:
    fecha_iso = fecha.isoformat() if isinstance(fecha, date) else str(fecha)
    cur = con.execute(
        "DELETE FROM tesis_indicadores_historial WHERE indicador_id = ? AND fecha = ?;",
        (indicador_id, fecha_iso),
    )
    if cur.rowcount == 0:
        raise LookupError(f"No hay lectura del indicador {indicador_id} con fecha {fecha_iso}")
    con.commit()


def _cargar(con, where: str = "", params: tuple = ()) -> list[dict]:
    """`where` es SIEMPRE un literal fijo de este modulo (nunca input del
    usuario); los valores van por `params`. Historial de cada indicador
    de la MAS RECIENTE a la mas vieja."""
    filas = con.execute(
        f"""
        SELECT i.id, a.ticker, i.nombre, i.fuente, i.frecuencia, i.direccion,
               i.umbral_verde, i.umbral_rojo, i.activo
        FROM tesis_indicadores i JOIN activos a ON a.id = i.activo_id
        {where}
        ORDER BY a.ticker, i.id;
        """,
        params,
    ).fetchall()
    if not filas:
        return []
    ids = [f[0] for f in filas]
    marcadores = ",".join("?" * len(ids))
    lecturas: dict[int, list[dict]] = {i: [] for i in ids}
    for ind_id, fecha, valor, estado in con.execute(
        f"""
        SELECT indicador_id, fecha, valor, estado FROM tesis_indicadores_historial
        WHERE indicador_id IN ({marcadores}) ORDER BY indicador_id, fecha DESC;
        """,
        ids,
    ):
        lecturas[ind_id].append({"fecha": fecha, "valor": valor, "estado": estado})

    out = []
    for ind_id, ticker, nombre, fuente, frecuencia, direccion, verde, rojo, activo in filas:
        hist = lecturas[ind_id]
        out.append({
            "id": ind_id, "ticker": ticker, "nombre": nombre, "fuente": fuente, "frecuencia": frecuencia,
            "direccion": direccion, "umbral_verde": verde, "umbral_rojo": rojo, "activo": bool(activo),
            "historial": hist,
            "ultima": hist[0] if hist else None,
            "estado_actual": hist[0]["estado"] if hist else None,
            "amarillo_dos_seguidos": len(hist) >= 2 and hist[0]["estado"] == "amarillo" and hist[1]["estado"] == "amarillo",
        })
    return out


def listar_indicadores(con, ticker: str) -> list[dict]:
    """Todos los indicadores del ticker (activos y archivados) con su historial."""
    return _cargar(con, "WHERE a.ticker = ?", (ticker,))


def indicadores_activos(con) -> list[dict]:
    """Indicadores activos de TODAS las posiciones (insumo del semaforo y de las alertas)."""
    return _cargar(con, "WHERE i.activo = 1")


def semaforo_de(indicadores: list[dict]) -> dict:
    """Peor estado entre los indicadores ACTIVOS con al menos una lectura.
    `total` cuenta los activos, `con_dato` los que tienen lectura."""
    activos = [i for i in indicadores if i["activo"]]
    con_dato = [i for i in activos if i["estado_actual"] is not None]
    peor = max((i["estado_actual"] for i in con_dato), key=_SEVERIDAD.__getitem__, default=None)
    return {"estado": peor, "total": len(activos), "con_dato": len(con_dato)}
