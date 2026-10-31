"""
PWOS -- Limpieza de picos aislados en el backfill de precio historico de
bonos via Rava (2026-08-26, ver backfill_precios_historico_bonos_rava_
2026-08-26.py y ESTADO_PWOS.md).

Motivo: la verificacion de sanidad pedida por el usuario antes de confiar en
los ~31.000 precios nuevos encontro picos de un SOLO dia que afectan a
varios bonos sin relacion entre si al mismo tiempo, siempre volviendo
al nivel normal al dia siguiente -- la firma de un glitch en el archivo
de Rava, no un evento de mercado real. Prueba mas clara: el 2021-11-04
AL30 (bono en ARS) cae 99,9% por un dia, pero AL30D (la MISMA deuda,
variante en dolares) no se mueve nada ese mismo dia -- si fuera un
evento real de mercado afectaria a ambas variantes.

Criterio de deteccion, ANCHO 1 (confirmado a mano contra 4 fechas reales
antes de escribir este script, no inventado): un precio en la fecha `i`
es un pico aislado si difiere MAS de 20% del precio en `i-1` Y MAS de
20% del precio en `i+1`, mientras que `i-1` e `i+1` difieren ENTRE SI
menos de 10% (los vecinos son consistentes, solo el punto del medio
no). Filtro generico aprobado por el usuario (no una lista de fechas
hardcodeada) -- puede dejar pasar algun caso ambiguo real (ej. un bono
CER con un ajuste legitimo de cotizacion en una fecha puntual) a cambio
de no depender de juicio caso por caso.

ANCHO 2 (agregado 2026-08-27 -- encontrado el mismo dia que ancho 1,
pero corrido ad-hoc sin guardarlo en un script versionado hasta ahora,
ver ESTADO_PWOS.md): mismo criterio, extendido a 2 puntos consecutivos
`i, i+1` -- ambos deben diferir MAS de 20% de `i-1` Y de `i+2`, mientras
`i-1` e `i+2` difieren ENTRE SI menos de 10%. Hacia falta porque un
detector de ancho 1 no puede ver un glitch que dura 2 ruedas seguidas
(ej. CUAP en 2026-03-04/05, ambos dias en $0,01 -- un valor centinela
obvio con $39.025/$38.150 de vecinos). Mismos 2 casos reales
encontrados en la sesion del 2026-08-26 (AL30 y CUAP, mismas 2 fechas).

Accion: DELETE de la fila puntual (no se reemplaza por una
interpolacion -- mejor un hueco real en la serie, que ya sabe manejar
el resto del codigo, que un numero inventado). Solo toca filas
`fuente='rava_historico'`.

Salvaguardas de siempre: backup + integrity_check antes, todo en una
transaccion, foreign_key_check despues, re-verificacion de que el
mismo detector ya no encuentra nada tras el DELETE.

Uso:
    python limpiar_picos_aislados_rava_2026-08-26.py
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()

UMBRAL_DESVIO_PUNTO = 0.20   # el punto del medio difiere mas de esto de CADA vecino
UMBRAL_CONSISTENCIA_VECINOS = 0.10  # los vecinos entre si difieren MENOS que esto


def detectar_picos_aislados(con) -> list[tuple[int, str, str, float, float, float]]:
    """(activo_id, ticker, fecha, precio_anterior, precio_punto, precio_siguiente)
    de cada pico aislado encontrado, fuente='rava_historico' unicamente."""
    filas = con.execute("""
        SELECT p.activo_id, a.ticker, p.fecha, p.precio
        FROM precios p JOIN activos a ON a.id = p.activo_id
        WHERE p.fuente = 'rava_historico'
        ORDER BY p.activo_id, p.fecha;
    """).fetchall()

    from collections import defaultdict
    por_activo = defaultdict(list)
    for activo_id, ticker, fecha, precio in filas:
        por_activo[activo_id].append((ticker, fecha, precio))

    picos = []
    for activo_id, serie in por_activo.items():
        for i in range(1, len(serie) - 1):
            ticker, f1, p1 = serie[i]
            _, f0, p0 = serie[i - 1]
            _, f2, p2 = serie[i + 1]
            if p0 <= 0 or p2 <= 0:
                continue
            vecinos_consistentes = abs(p2 - p0) / max(p0, 1e-9) < UMBRAL_CONSISTENCIA_VECINOS
            punto_lejos_de_p0 = abs(p1 - p0) / max(p0, 1e-9) > UMBRAL_DESVIO_PUNTO
            punto_lejos_de_p2 = abs(p1 - p2) / max(p2, 1e-9) > UMBRAL_DESVIO_PUNTO
            if vecinos_consistentes and punto_lejos_de_p0 and punto_lejos_de_p2:
                picos.append((activo_id, ticker, f1, p0, p1, p2))
    return picos


def detectar_picos_aislados_ancho2(con) -> list[tuple[int, str, str, str, float, float, float, float]]:
    """(activo_id, ticker, fecha_1, fecha_2, precio_anterior, precio_1,
    precio_2, precio_siguiente) de cada par de fechas consecutivas que
    juntas forman un pico aislado de ANCHO 2 -- ver docstring del
    modulo. `fuente='rava_historico'` unicamente."""
    filas = con.execute("""
        SELECT p.activo_id, a.ticker, p.fecha, p.precio
        FROM precios p JOIN activos a ON a.id = p.activo_id
        WHERE p.fuente = 'rava_historico'
        ORDER BY p.activo_id, p.fecha;
    """).fetchall()

    from collections import defaultdict
    por_activo = defaultdict(list)
    for activo_id, ticker, fecha, precio in filas:
        por_activo[activo_id].append((ticker, fecha, precio))

    picos = []
    for activo_id, serie in por_activo.items():
        for i in range(1, len(serie) - 2):
            ticker, f0, p0 = serie[i - 1]
            _, f1, p1 = serie[i]
            _, f2, p2 = serie[i + 1]
            _, f3, p3 = serie[i + 2]
            if p0 <= 0 or p3 <= 0:
                continue
            vecinos_consistentes = abs(p3 - p0) / max(p0, 1e-9) < UMBRAL_CONSISTENCIA_VECINOS
            ambos_lejos = (
                abs(p1 - p0) / max(p0, 1e-9) > UMBRAL_DESVIO_PUNTO
                and abs(p1 - p3) / max(p3, 1e-9) > UMBRAL_DESVIO_PUNTO
                and abs(p2 - p0) / max(p0, 1e-9) > UMBRAL_DESVIO_PUNTO
                and abs(p2 - p3) / max(p3, 1e-9) > UMBRAL_DESVIO_PUNTO
            )
            if vecinos_consistentes and ambos_lejos:
                picos.append((activo_id, ticker, f1, f2, p0, p1, p2, p3))
    return picos


def main():
    con = sqlite3.connect(DB_PATH)

    picos1 = detectar_picos_aislados(con)
    print(f"Picos aislados (ancho 1) detectados: {len(picos1)}")
    for activo_id, ticker, fecha, p0, p1, p2 in sorted(picos1, key=lambda x: x[2]):
        print(f"  {ticker:<8} {fecha}  anterior={p0:>12.4f}  PUNTO={p1:>12.4f}  siguiente={p2:>12.4f}")

    picos2 = detectar_picos_aislados_ancho2(con)
    print(f"\nPicos aislados (ancho 2) detectados: {len(picos2)}")
    for activo_id, ticker, f1, f2, p0, p1, p2, p3 in sorted(picos2, key=lambda x: x[2]):
        print(f"  {ticker:<8} {f1}/{f2}  anterior={p0:>12.4f}  PUNTOS={p1:.4f}/{p2:.4f}  siguiente={p3:>12.4f}")

    if not picos1 and not picos2:
        print("Nada para limpiar.")
        con.close()
        return

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-limpieza-picos-rava_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    if backup_path.exists():
        raise RuntimeError(f"{backup_path} ya existe - no lo piso, revisar a mano")
    shutil.copy2(DB_PATH, backup_path)
    con_chk = sqlite3.connect(backup_path)
    ok = con_chk.execute("PRAGMA integrity_check;").fetchone()[0]
    con_chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"\nBackup OK: {backup_path} (integrity_check=ok)")

    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("BEGIN;")
    try:
        borrados = 0
        for activo_id, ticker, fecha, p0, p1, p2 in picos1:
            cur = con.execute(
                "DELETE FROM precios WHERE activo_id = ? AND fecha = ? AND fuente = 'rava_historico';",
                (activo_id, fecha),
            )
            borrados += cur.rowcount
        for activo_id, ticker, f1, f2, p0, p1, p2, p3 in picos2:
            cur = con.execute(
                "DELETE FROM precios WHERE activo_id = ? AND fecha IN (?, ?) AND fuente = 'rava_historico';",
                (activo_id, f1, f2),
            )
            borrados += cur.rowcount

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"\nCOMMIT OK. Filas borradas: {borrados}")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise

    restantes1 = detectar_picos_aislados(con)
    restantes2 = detectar_picos_aislados_ancho2(con)
    print(f"\nRe-verificacion con el mismo detector: {len(restantes1)} picos ancho 1 + {len(restantes2)} picos ancho 2 restantes (deberia ser 0 y 0).")
    con.close()


if __name__ == "__main__":
    main()
