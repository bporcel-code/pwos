"""
PWOS -- Carga bonos_referencia + cronograma_pagos_bonos desde Rava
(database/rava_client.py) para la renta fija vigente en pwos.db.

Re-ejecutable (Paso 5 del pedido original): UPSERT, no INSERT ciego --
correr esto de nuevo (via n8n cuando aparezca un bono nuevo en la
cartera, mismo patron que el resto del pipeline) actualiza el registro
existente en vez de duplicar filas. `fuente`/`actualizado_en` quedan
para poder distinguir mas adelante un ticker cargado por Rava de uno
completado a mano desde el Sheet.

Tolerante a fallos por ticker (Paso 4): si Rava no tiene cobertura para
un ticker (o la respuesta viene incompleta), se loguea y se sigue con
el resto del batch -- no aborta toda la carga por un solo ticker.
Confirmado con dry_run_bonos_rava.py: 22/22 cobertura contra la cartera
real al momento de escribir esto, pero un bono nuevo en el futuro podria
no tener cobertura (ej. una ON muy chica sin listar en Rava).
"""
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import configuracion
from rava_client import obtener_flujo_bono, obtener_referencia_bonos

DB_PATH = configuracion.ruta_db()

TIPOS_RENTA_FIJA = ("bono_soberano", "bono_provincial", "bono_cer", "on", "lecap")

# Moneda de pago REAL del cronograma -- mapa explicito, verificado a
# mano, NO el campo `es_dolar` de Rava (2026-08-12: encontramos que no
# es confiable para esto -- ver docstring de migrar_v26_moneda_pago_bonos.py).
# Verificado por texto del nombre completo del bono en Rava (denota la
# moneda real del instrumento) + cruzado contra la renta realmente cobrada
# (pago en USD via la especie "{TICKER} US$" del mismo bono).
#
# Un dollar-linked es un caso distinto (NO es una variante de ARS
# -- decision del usuario, 2026-08-12): el nombre de Rava dice "u$s-linked".
# Confirmado contra la respuesta cruda de la API (2026-08-12): `cupon`
# de un dollar-linked viene en pesos (ej. ~1.500 por VN 100 en un cupon
# futuro -- un cupon real en USD de esa magnitud seria ~1500% period,
# absurdo para un step-up de esta clase). Se agrega la categoria
# 'USD_LINKED' con logica propia en vista_flujo_fondos_bonos: convierte
# contra USD_A3500 (desde 2026-10-14 la serie oficial del BCRA, variable
# 5 de la API v4, con historia; ver cargar_usd_a3500.py),
# NO contra MEP -- un dollar-linked se liquida con la formula de la
# Comunicacion A3500, mezclarlo con MEP le suma de gratis la brecha
# MEP-A3500 a un instrumento que no tiene relacion con esa brecha.
# Auditados los 22 tickers de bonos_referencia por "linked"/"vinculad"
# en el nombre: hubo un solo caso (una ON dollar-linked).
#
# Si aparece un ticker nuevo que no esta en este mapa, NO se adivina:
# queda sin moneda_pago (NULL) y se excluye del indicador de flujo de
# fondos en USD hasta clasificarlo a mano (mismo criterio que
# MONEDA_PRECIO_DATA912 en dry_run_precios.py).
MONEDA_PAGO_BONOS = configuracion.get("bonos", "moneda_pago", default={})


def hacer_backup():
    # Hueco real encontrado 2026-08-21, corrido cada semana desde n8n
    # (POST /api/refrescar-bonos-rava) SIN que nadie lo notara: este
    # script corre DENTRO del contenedor Docker de pwos-api, donde
    # Path(__file__).parent es /app -- una ruta que NO esta montada como
    # volumen (solo /app/pwos.db y /app/descargas lo estan). El backup
    # se escribia en la capa efimera del contenedor: el script imprimia
    # "Backup OK" (el archivo existe y pasa integrity_check EN ESE
    # INSTANTE), pero se pierde en el primer `docker rm`/recreacion del
    # contenedor -- que pasa seguido en este proyecto (cada vez que se
    # reconstruye la imagen). Ningun backup de esta rutina semanal
    # sobrevivio mas alla de su propia corrida desde que empezo a
    # correr por n8n (2026-08-13) -- los unicos backups reales de este
    # script en disco son de ANTES de esa fecha, corridos a mano en el
    # host, ver ESTADO_PWOS.md.
    #
    # Fix: BACKUP_DIR calculado ACA (no a nivel de modulo, mismo motivo
    # que el fix de importador_precios.py el mismo dia) + `/app/backups`
    # ahora SI esta montado a la carpeta de backups del host (ver docker run /
    # Dockerfile) -- pero igual se calcula relativo a DB_PATH.parent en
    # vez de hardcodear "/app/backups", para que este script siga
    # funcionando igual si algun dia corre fuera de Docker.
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-bonos-rava_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(DB_PATH, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


def tickers_renta_fija_vigente(con) -> list[str]:
    filas = con.execute(
        f"""
        SELECT DISTINCT ticker FROM vista_posiciones
        WHERE tipo_activo IN ({",".join("?" * len(TIPOS_RENTA_FIJA))})
        ORDER BY ticker;
        """,
        TIPOS_RENTA_FIJA,
    ).fetchall()
    return [f[0] for f in filas]


def cargar(db_path: Path = DB_PATH) -> None:
    hacer_backup()

    con = sqlite3.connect(db_path, isolation_level=None)  # autocommit off -> control manual
    con.execute("PRAGMA foreign_keys = ON;")

    tickers = tickers_renta_fija_vigente(con)
    print(f"Tickers de renta fija vigentes: {len(tickers)}")

    referencia_rava = obtener_referencia_bonos()
    print(f"Listado de Rava: {len(referencia_rava)} especies.\n")

    ahora = datetime.now(timezone.utc).isoformat()
    ok, fallidos = [], []

    try:
        con.execute("BEGIN;")

        for ticker in tickers:
            activo_id_row = con.execute(
                "SELECT id FROM activos WHERE ticker = ?;", (ticker,)
            ).fetchone()
            if activo_id_row is None:
                # No deberia pasar (el ticker sale de vista_posiciones, que
                # ya hace JOIN contra activos) -- si pasa, es una senal de
                # inconsistencia real, no se ignora en silencio.
                raise RuntimeError(f"{ticker}: no se encontro en 'activos' pese a estar en vista_posiciones")
            activo_id = activo_id_row[0]

            ref = referencia_rava.get(ticker)
            flujo = obtener_flujo_bono(ticker)

            if ref is None or flujo is None:
                print(f"{ticker:8} FALTA -- referencia={'si' if ref else 'NO'}  flujo={'si' if flujo else 'NO'}  (revisar manual / Sheet)")
                fallidos.append(ticker)
                continue

            moneda_pago = MONEDA_PAGO_BONOS.get(ticker)
            if moneda_pago is None:
                print(f"         AVISO: {ticker} sin moneda_pago clasificada -- queda NULL, se excluye del indicador de flujo en USD")

            con.execute(
                """
                INSERT INTO bonos_referencia
                    (activo_id, nombre, tipo_instrumento, legislacion, es_dolar, moneda_pago, fuente, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?, 'rava', ?)
                ON CONFLICT (activo_id) DO UPDATE SET
                    nombre = excluded.nombre,
                    tipo_instrumento = excluded.tipo_instrumento,
                    legislacion = excluded.legislacion,
                    es_dolar = excluded.es_dolar,
                    moneda_pago = excluded.moneda_pago,
                    fuente = 'rava',
                    actualizado_en = excluded.actualizado_en;
                """,
                (activo_id, ref["nombre"].strip(), ref["tipo"], ref["ley"], int(flujo["esDolar"]), moneda_pago, ahora),
            )

            # Bug real encontrado 2026-09-07 (Epica 11): el UPSERT por
            # (activo_id, fecha_pago, tipo_pago) dejaba filas HUERFANAS
            # cuando Rava corrige la fecha exacta de un pago (ajuste de
            # dia habil, unos pocos dias) entre 2 cargas -- la fila vieja
            # nunca se borraba, la nueva se insertaba aparte, duplicando
            # el pago. Confirmado en varios bonos (110 de amortizacion en vez de
            # 100, 128,57, o 200 -- doble amortizacion
            # completa). Fix: el flujo de Rava es la verdad completa en
            # cada carga -- se borra todo el cronograma existente del
            # activo antes de reinsertar, no se confia en que las fechas
            # viejas sigan siendo las mismas.
            con.execute("DELETE FROM cronograma_pagos_bonos WHERE activo_id = ?;", (activo_id,))

            n_pagos = 0
            for evento in flujo["flujos"]:
                fecha_pago = evento["fecha_pago"][:10]  # 'YYYY-MM-DDTHH:MM:SS...Z' -> 'YYYY-MM-DD'
                for tipo_pago, campo in (("interes", "cupon"), ("amortizacion", "principal")):
                    monto = float(evento[campo])
                    if monto == 0:
                        continue  # sin pago de este tipo en esta fecha, no se inserta fila vacia
                    con.execute(
                        """
                        INSERT INTO cronograma_pagos_bonos
                            (activo_id, fecha_pago, tipo_pago, monto_por_vn, fuente, actualizado_en)
                        VALUES (?, ?, ?, ?, 'rava', ?)
                        ON CONFLICT (activo_id, fecha_pago, tipo_pago) DO UPDATE SET
                            monto_por_vn = excluded.monto_por_vn,
                            fuente = 'rava',
                            actualizado_en = excluded.actualizado_en;
                        """,
                        (activo_id, fecha_pago, tipo_pago, monto, ahora),
                    )
                    n_pagos += 1

            print(f"{ticker:8} OK    tipo={ref['tipo']:<20} ley={ref['ley']:<4} eventos_de_pago={n_pagos}")
            ok.append(ticker)

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nCarga OK.")
        print(f"  OK: {len(ok)}/{len(tickers)} -> {ok}")
        if fallidos:
            print(f"  Sin cobertura en Rava (pendiente manual): {fallidos}")
        print("  foreign_key_check: sin filas rotas")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    cargar()
