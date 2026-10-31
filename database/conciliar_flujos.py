"""
PWOS -- conciliacion estimado vs. real del flujo de cobros (ticket "Flujo de
pagos mensual: estimado vs. real", Tanda 3, 2026-10-05). Schema v2.45/v2.46,
sin migracion nueva.

Cada estimado generado por flujo_estimado.py (bonos, dividendos, alquileres)
que ya vencio se busca entre los movimientos REALES que cargo la ronda
incremental, en el MISMO contenedor, dentro de una ventana de dias y con
moneda compatible:
  - cupon / amortizacion / dividendo: movimientos `renta` en efectivo, sin
    caucion. Si la descripcion trae el ticker del estimado ("Pago de
    Renta(AL30 US$)", "RTA GD30 ...", ".../ TICKER", "Dividendos (KO)")
    el candidato es ATRIBUIDO y se acepta con cualquier monto razonable;
    si no trae ningun ticker (ej. "Nota De Credito Conversion" para los
    dividendos, "Renta Y Amortizacion - Peso argentino") solo se acepta si
    el monto se parece (TOLERANCIA_SIN_ATRIBUIR). Si trae OTRO ticker, no es
    candidato.
  - alquiler: movimientos `ingreso` del contenedor de cobro (una
    transferencia de quien alquila o de la inmobiliaria, sin concepto
    fijo) con monto parecido (TOLERANCIA_ALQUILER), excluyendo sueldo y
    transferencias desde brokers. Entre dos alquileres de monto parecido
    en el mismo contenedor decide la fecha. No se cambia el `tipo` del movimiento: como
    `ingreso` no entra en el interes bancario (`flujos_otros_pasivos` solo
    lee `renta`) ni en los flujos externos de rendimiento.py (patrones de
    brokers), asi que no se cuenta dos veces.

Regla "lo ambiguo bloquea": se vincula SOLO si el mejor candidato no empata
con otro (mismo nivel de atribucion, monto parecido y fecha a menos de 3
dias) ni lo reclama otro estimado igual de bien. Los empates quedan
"a confirmar" con la lista de movimientos posibles, y se resuelven a mano:
    python conciliar_flujos.py --vincular FLUJO_ID MOVIMIENTO_ID

Al vincular: estado 'cobrado' + movimiento_id; se borra la fila
'importador' que el importador de un broker crea para el mismo movimiento (la
misma plata, sin datos extra); en un alquiler se carga ademas la fila real
en `alquileres` (solapa Real Estate), con el MEP de la fecha de cobro.

'no_cobrado': solo si el contenedor ya tiene movimientos cargados POSTERIORES
al fin de la ventana (la ronda cubrio ese periodo) y no aparecio ningun
candidato. Sin ronda que cubra la fecha, el estimado sigue 'a confirmar'. Un
no_cobrado se puede vincular igual si el pago llega tarde.

Uso (parado en database/):
    python conciliar_flujos.py                    # concilia y muestra el estado
    python conciliar_flujos.py --dry-run          # muestra que haria, sin escribir
    python conciliar_flujos.py --vincular 412 5790
    python conciliar_flujos.py --no-cobrado 412
    python conciliar_flujos.py --desvincular 412  # vuelve a pendiente
"""
import argparse
import math
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import configuracion
from dashboard import obtener_precio_fx, precio_fx_historico
from renta_dividendos import _es_caucion
from valorizacion import parte_propia, pct_de, pct_propiedad_por_cuenta, solo_cierre

DB_PATH = configuracion.ruta_db()

ORIGENES_CONCILIABLES = ("cronograma", "yfinance", "alquiler", "manual")
# Ventana (dias antes, dias despues) alrededor de la fecha estimada.
VENTANAS = {
    "cupon": (4, 10), "amortizacion": (4, 10),
    "dividendo": (7, 20),      # la acreditacion de un CEDEAR varia (algun broker convierte dias despues)
    "alquiler": (6, 10),
}
# Rango aceptado de real/estimado.
RANGO_ATRIBUIDO = (0.3, 3.0)
TOLERANCIA_SIN_ATRIBUIR = (0.75, 1.30)
TOLERANCIA_ALQUILER = (0.70, 1.40)
# Un ingreso con alguno de estos textos NO es un alquiler (sueldo, plata que
# vuelve de un broker o de otra cuenta propia del usuario).
EXCLUIR_ALQUILER = tuple(configuracion.get("movimientos", "textos_no_alquiler", default=[]))
DIAS_EMPATE = 3
CATEGORIA_ALQUILER = "Alquileres"  # migrar_v250_contenedores_compartidos.py


def _ticker_en(descripcion: str, ticker: str) -> bool:
    return re.search(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", descripcion.upper()) is not None


def _tickers_conocidos(con) -> set[str]:
    return {t for (t,) in con.execute("SELECT ticker FROM activos;") if len(t) >= 3}


def _moneda_compatible(estimada: str, real: str) -> bool:
    return (estimada == "USD" and real in ("USD", "USD_CCL")) or (estimada == real)


def _estimados_a_conciliar(con, hoy: date) -> list[dict]:
    marcadores = ",".join("?" * len(ORIGENES_CONCILIABLES))
    filas = con.execute(
        f"""
        SELECT f.id, f.tipo_flujo, f.fecha_estimada, f.monto_estimado, f.moneda, f.contenedor_id,
               f.estado, a.ticker, f.propiedad_id
        FROM flujos_esperados f
        LEFT JOIN activos a ON a.id = f.activo_id
        WHERE f.origen_estimado IN ({marcadores}) AND f.estado IN ('pendiente', 'no_cobrado')
          AND f.contenedor_id IS NOT NULL AND f.fecha_estimada <= ?;
        """,
        (*ORIGENES_CONCILIABLES, (hoy + timedelta(days=max(v[0] for v in VENTANAS.values()))).isoformat()),
    ).fetchall()
    claves = ("id", "tipo", "fecha", "monto", "moneda", "contenedor_id", "estado", "ticker", "propiedad_id")
    return [dict(zip(claves, f)) for f in filas]


def _movimientos_libres(con, contenedor_id: int, tipo_mov: str, desde: str, hasta: str,
                        categoria: str | None = None) -> list[dict]:
    """`categoria`: solo los movimientos con esa categoria (raiz). `monto_propio`
    = la parte propia (contenedor compartido, ADR-032); `monto` es el 100%."""
    filas = con.execute(
        """
        SELECT m.id, m.fecha, m.monto, m.moneda, m.descripcion, co.nombre, c.id
        FROM movimientos m
        JOIN cuentas c ON c.id = m.cuenta_destino_id
        JOIN contenedores co ON co.id = c.contenedor_id
        LEFT JOIN categorias cat ON cat.id = m.categoria_id
        WHERE c.contenedor_id = ? AND m.tipo = ? AND m.naturaleza = 'efectivo' AND m.monto > 0
          AND m.fecha BETWEEN ? AND ?
          AND (? IS NULL OR cat.nombre = ?)
          AND NOT EXISTS (SELECT 1 FROM flujos_esperados f
                          WHERE f.movimiento_id = m.id AND f.origen_estimado <> 'importador');
        """,
        (contenedor_id, tipo_mov, desde, hasta, categoria, categoria),
    ).fetchall()
    pcts = pct_propiedad_por_cuenta(con)
    claves = ("id", "fecha", "monto", "moneda", "descripcion", "contenedor")
    return [{**dict(zip(claves, f[:6])), "monto_propio": parte_propia(f[2], pct_de(pcts, f[6]))} for f in filas]


def candidatos(con, e: dict, tickers: set[str]) -> list[dict]:
    """Movimientos reales que podrian ser el cobro `e`, con su puntaje
    (menor = mejor): (0 atribuido / 1 sin atribuir, desvio de monto en
    tramos de 5%, dias de distancia)."""
    antes, despues = VENTANAS[e["tipo"]]
    fecha = date.fromisoformat(e["fecha"])
    desde, hasta = (fecha - timedelta(days=antes)).isoformat(), (fecha + timedelta(days=despues)).isoformat()
    salida = []
    if e["tipo"] == "alquiler":
        # Un alquiler llega como `ingreso` (una transferencia de quien cobra o
        # de la inmobiliaria) o como `renta` con categoria "Alquileres" (cuenta
        # cuenta compartida, al 100%: se compara la parte propia, ADR-032).
        movimientos = (_movimientos_libres(con, e["contenedor_id"], "ingreso", desde, hasta)
                       + _movimientos_libres(con, e["contenedor_id"], "renta", desde, hasta, CATEGORIA_ALQUILER))
        for m in movimientos:
            ratio = m["monto_propio"] / e["monto"]
            if (not _moneda_compatible(e["moneda"], m["moneda"])
                    or any(x in m["descripcion"].lower() for x in EXCLUIR_ALQUILER)
                    or not TOLERANCIA_ALQUILER[0] <= ratio <= TOLERANCIA_ALQUILER[1]):
                continue
            salida.append({**m, "atribuido": False, "ratio": ratio})
    else:
        for m in _movimientos_libres(con, e["contenedor_id"], "renta", desde, hasta):
            if not _moneda_compatible(e["moneda"], m["moneda"]) or _es_caucion(m["contenedor"], m["descripcion"].lower()):
                continue
            ratio = m["monto"] / e["monto"]
            if _ticker_en(m["descripcion"], e["ticker"]):
                if not RANGO_ATRIBUIDO[0] <= ratio <= RANGO_ATRIBUIDO[1]:
                    continue
                atribuido = True
            elif any(_ticker_en(m["descripcion"], t) for t in tickers):
                continue  # es de otro ticker
            elif TOLERANCIA_SIN_ATRIBUIR[0] <= ratio <= TOLERANCIA_SIN_ATRIBUIR[1]:
                atribuido = False
            else:
                continue
            salida.append({**m, "atribuido": atribuido, "ratio": ratio})
    for c in salida:
        dias = abs((date.fromisoformat(c["fecha"]) - fecha).days)
        c["dias"] = dias
        c["puntaje"] = (0 if c["atribuido"] else 1, round(abs(math.log(c["ratio"])) / 0.05), dias)
    return sorted(salida, key=lambda c: c["puntaje"])


def _empatan(a: tuple, b: tuple) -> bool:
    return a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) < DIAS_EMPATE


def _cobertura(con, contenedor_id: int) -> str | None:
    return con.execute(
        """
        SELECT MAX(m.fecha) FROM movimientos m
        JOIN cuentas c ON c.id IN (m.cuenta_origen_id, m.cuenta_destino_id)
        WHERE c.contenedor_id = ?;
        """,
        (contenedor_id,),
    ).fetchone()[0]


def planificar(con, hoy: date | None = None) -> dict:
    """Que vincularia / marcaria, SIN escribir. Devuelve
    {"vincular": [(flujo_id, mov)], "ambiguos": {flujo_id: [movs]}, "no_cobrado": [flujo_id]}."""
    hoy = hoy or configuracion.hoy()
    tickers = _tickers_conocidos(con)
    estimados = [e for e in _estimados_a_conciliar(con, hoy)]
    cands = {e["id"]: candidatos(con, e, tickers) for e in estimados}
    por_id = {e["id"]: e for e in estimados}

    # Greedy global por puntaje, con chequeo de empates en los dos sentidos.
    pares = sorted(((c["puntaje"], e_id, c) for e_id, cs in cands.items() for c in cs), key=lambda p: (p[0], p[1]))
    vincular, ambiguos, usados_e, usados_m = [], {}, set(), set()
    for puntaje, e_id, c in pares:
        if e_id in usados_e or c["id"] in usados_m or e_id in ambiguos:
            continue
        rivales_m = [o for o in cands[e_id] if o["id"] != c["id"] and o["id"] not in usados_m and _empatan(o["puntaje"], puntaje)]
        rivales_e = [o_id for o_id, cs in cands.items()
                     if o_id != e_id and o_id not in usados_e
                     and any(x["id"] == c["id"] and _empatan(x["puntaje"], puntaje) for x in cs)]
        if rivales_m or rivales_e:
            ambiguos[e_id] = [x for x in cands[e_id] if x["id"] not in usados_m]
            for o_id in rivales_e:
                ambiguos.setdefault(o_id, [x for x in cands[o_id] if x["id"] not in usados_m])
            continue
        vincular.append((e_id, c))
        usados_e.add(e_id)
        usados_m.add(c["id"])

    no_cobrado = []
    for e in estimados:
        if e["id"] in usados_e or e["id"] in ambiguos or cands[e["id"]] or e["estado"] == "no_cobrado":
            continue
        fin_ventana = date.fromisoformat(e["fecha"]) + timedelta(days=VENTANAS[e["tipo"]][1])
        cobertura = _cobertura(con, e["contenedor_id"])
        if cobertura and cobertura > fin_ventana.isoformat() and fin_ventana < hoy:
            no_cobrado.append(e["id"])
    return {"vincular": vincular, "ambiguos": ambiguos, "no_cobrado": no_cobrado, "estimados": por_id}


@solo_cierre()  # historia / escribe en la base: siempre con el cierre (ADR-033)
def _vincular(con, flujo_id: int, movimiento_id: int) -> None:
    con.execute("UPDATE flujos_esperados SET estado = 'cobrado', movimiento_id = ? WHERE id = ?;",
                (movimiento_id, flujo_id))
    con.execute("DELETE FROM flujos_esperados WHERE origen_estimado = 'importador' AND movimiento_id = ?;",
                (movimiento_id,))
    fila = con.execute(
        """SELECT f.propiedad_id, f.fecha_estimada, m.monto, m.moneda, m.fecha, m.cuenta_destino_id
           FROM flujos_esperados f JOIN movimientos m ON m.id = ?
           WHERE f.id = ? AND f.tipo_flujo = 'alquiler';""",
        (movimiento_id, flujo_id),
    ).fetchone()
    if fila is not None:
        propiedad_id, fecha_est, monto, moneda, fecha_cobro, cuenta_id = fila
        # `alquileres` guarda la parte NETA del usuario (ADR-026); en la cuenta
        # compartida el movimiento esta al 100% (ADR-032).
        monto = round(parte_propia(monto, pct_de(pct_propiedad_por_cuenta(con), cuenta_id)), 2)
        fx = precio_fx_historico(con, "USD_MEP", fecha_cobro) or obtener_precio_fx(con, "USD_MEP")
        con.execute(
            """INSERT OR IGNORE INTO alquileres (propiedad_id, periodo, monto, moneda, monto_usd, fecha_cobro)
               VALUES (?, ?, ?, ?, ?, ?);""",
            (propiedad_id, fecha_est[:7], monto, moneda, round(monto if moneda == "USD" else monto / fx, 2), fecha_cobro),
        )


def conciliar(con, hoy: date | None = None) -> dict:
    """Aplica `planificar` en una transaccion. No escribe si no hay nada.
    Devuelve los conteos y los ambiguos (flujo_id -> movimientos posibles)."""
    plan = planificar(con, hoy)
    if plan["vincular"] or plan["no_cobrado"]:
        with con:
            for flujo_id, mov in plan["vincular"]:
                _vincular(con, flujo_id, mov["id"])
            con.executemany("UPDATE flujos_esperados SET estado = 'no_cobrado' WHERE id = ? AND estado = 'pendiente';",
                            [(i,) for i in plan["no_cobrado"]])
    return {"vinculados": len(plan["vincular"]), "no_cobrados": len(plan["no_cobrado"]),
            "ambiguos": {e_id: [{"id": m["id"], "fecha": m["fecha"], "monto": m["monto"], "moneda": m["moneda"],
                                 "descripcion": m["descripcion"]} for m in ms]
                         for e_id, ms in plan["ambiguos"].items()}}


def vincular_manual(con, flujo_id: int, movimiento_id: int) -> None:
    e = con.execute("SELECT estado, movimiento_id FROM flujos_esperados WHERE id = ?;", (flujo_id,)).fetchone()
    if e is None:
        raise ValueError(f"no existe el flujo {flujo_id}")
    if e[0] == "cobrado":
        raise ValueError(f"el flujo {flujo_id} ya esta cobrado (movimiento {e[1]}); --desvincular primero")
    if con.execute("SELECT 1 FROM movimientos WHERE id = ?;", (movimiento_id,)).fetchone() is None:
        raise ValueError(f"no existe el movimiento {movimiento_id}")
    otro = con.execute("SELECT id FROM flujos_esperados WHERE movimiento_id = ? AND origen_estimado <> 'importador';",
                       (movimiento_id,)).fetchone()
    if otro:
        raise ValueError(f"el movimiento {movimiento_id} ya esta vinculado al flujo {otro[0]}")
    with con:
        _vincular(con, flujo_id, movimiento_id)


def desvincular(con, flujo_id: int) -> None:
    fila = con.execute(
        """SELECT f.tipo_flujo, f.propiedad_id, f.fecha_estimada, m.fecha, m.monto
           FROM flujos_esperados f LEFT JOIN movimientos m ON m.id = f.movimiento_id WHERE f.id = ?;""",
        (flujo_id,),
    ).fetchone()
    if fila is None:
        raise ValueError(f"no existe el flujo {flujo_id}")
    with con:
        if fila[0] == "alquiler" and fila[3] is not None:
            con.execute("DELETE FROM alquileres WHERE propiedad_id = ? AND periodo = ? AND fecha_cobro = ? AND monto = ?;",
                        (fila[1], fila[2][:7], fila[3], fila[4]))
        con.execute("UPDATE flujos_esperados SET estado = 'pendiente', movimiento_id = NULL WHERE id = ?;", (flujo_id,))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--vincular", nargs=2, type=int, metavar=("FLUJO_ID", "MOVIMIENTO_ID"))
    ap.add_argument("--no-cobrado", type=int, metavar="FLUJO_ID")
    ap.add_argument("--desvincular", type=int, metavar="FLUJO_ID")
    args = ap.parse_args()
    con = sqlite3.connect(args.db)

    if args.vincular:
        vincular_manual(con, *args.vincular)
        print(f"Flujo {args.vincular[0]} vinculado al movimiento {args.vincular[1]}.")
        return
    if args.no_cobrado:
        with con:
            con.execute("UPDATE flujos_esperados SET estado = 'no_cobrado', movimiento_id = NULL WHERE id = ?;", (args.no_cobrado,))
        print(f"Flujo {args.no_cobrado} marcado como no cobrado.")
        return
    if args.desvincular:
        desvincular(con, args.desvincular)
        print(f"Flujo {args.desvincular} vuelve a pendiente.")
        return

    plan = planificar(con)
    est = plan["estimados"]

    def etiqueta(e_id):
        e = est[e_id]
        return f"[{e_id}] {e['fecha']} {e['ticker'] or 'alquiler'} {e['tipo']} {e['moneda']} {e['monto']:,.2f}"

    print(f"Vincular ({len(plan['vincular'])}):")
    for e_id, m in plan["vincular"]:
        print(f"  {etiqueta(e_id)}  <-  mov {m['id']} {m['fecha']} {m['moneda']} {m['monto']:,.2f}  {m['descripcion'][:60]}")
    print(f"Ambiguos, a confirmar con --vincular ({len(plan['ambiguos'])}):")
    for e_id, ms in plan["ambiguos"].items():
        print(f"  {etiqueta(e_id)}")
        for m in ms:
            print(f"      mov {m['id']} {m['fecha']} {m['moneda']} {m['monto']:,.2f}  {m['descripcion'][:60]}")
    print(f"No cobrados ({len(plan['no_cobrado'])}): " + ", ".join(etiqueta(i) for i in plan["no_cobrado"]))
    if args.dry_run:
        print("(dry-run: no se escribio nada)")
        return
    r = conciliar(con)
    print(f"Aplicado: {r['vinculados']} vinculados, {r['no_cobrados']} no cobrados.")


if __name__ == "__main__":
    main()
