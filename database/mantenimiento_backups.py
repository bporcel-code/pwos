"""
PWOS -- Mantenimiento de backups: compresion + retencion (2026-09-30).

Problema (medido el 2026-09-30): los scripts copian `pwos.db` entero antes
de escribir y nadie borra esas copias -- 210 archivos, 2,25 GB en un
volumen VeraCrypt de 5 GB. Con la base en 43 MB (Epica 21) y ~5 procesos
diarios que hacen backup, eran ~215 MB por dia: V: se llenaba en ~2
semanas y ahi fallan TODAS las escrituras de SQLite.

Politica (decidida por el usuario, 2026-09-30):
- Comprimir con gzip todo backup de mas de 24 h que se conserve (un
  backup de 43 MB queda en ~6,5 MB). El original se borra SOLO despues de
  releer el .gz entero (gzip verifica el CRC) y confirmar el mismo tamanio.
- Retencion. Se CONSERVA un backup si cumple cualquiera de estas:
    * tiene menos de 24 h;
    * es el mas reciente de su tipo (nunca se queda un tipo sin backup);
    * tipo RECURRENTE (archivos en 2 o mas dias distintos: snapshot de
      bonos, benchmarks, precios, puesta al dia, rondas de brokers...):
      esta entre los 7 mas recientes, o es el ultimo de su semana ISO y
      tiene 8 semanas o menos;
    * tipo UNICO (una migracion, una correccion puntual): tiene 180 dias
      o menos.
  Todo lo demas se borra. Un tipo nuevo o desconocido cae en "unico"
  (180 dias): el caso mas conservador.
- El tipo sale del nombre (`pwos_backup_pre-precios_2026-09-28_1805.db` ->
  `pwos_backup_pre-precios`); la fecha, del nombre (si no tiene, la de
  modificacion del archivo).

Por defecto SOLO muestra lo que haria. `--aplicar` comprime y borra, y
deja una linea por accion en `mantenimiento_backups.log` (en la misma
carpeta). Salida con `RESUMEN_JSON:` para POST /api/mantenimiento-backups.

Restaurar un backup comprimido (genera el .db al lado del .gz, nunca toca
pwos.db):
    python mantenimiento_backups.py --descomprimir "<carpeta de backups>/ARCHIVO.db.gz"

Uso:
    python mantenimiento_backups.py              # simulacion
    python mantenimiento_backups.py --aplicar
    python mantenimiento_backups.py --dir CARPETA
"""
import argparse
import gzip
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import configuracion

# En el contenedor pwos-api, database/pwos.db y backups/ son bind mounts.
# Fuera de Docker, database/pwos.db es un stub de 0 bytes y database/backups/
# puede ser una carpeta vieja distinta: se usa la de la configuracion.
BACKUPS_DIR = configuracion.ruta_backups()
LOG = "mantenimiento_backups.log"

HORAS_GRACIA = 24
ULTIMOS_RECURRENTE = 7
SEMANAS_RECURRENTE = 8
DIAS_UNICO = 180

_PATRON = re.compile(r"^(?P<tipo>.+?)_?(?P<fecha>\d{4}-\d{2}-\d{2})(?:_(?P<hora>\d{4,6}))?")


@dataclass
class Backup:
    nombre: str             # nombre del archivo (con .gz si esta comprimido)
    tipo: str
    fecha: datetime
    tamanio: int
    comprimido: bool


def clasificar(nombre: str, tamanio: int, mtime: datetime) -> Backup:
    base = nombre[:-3] if nombre.endswith(".gz") else nombre
    m = _PATRON.match(base)
    if m:
        hora = (m["hora"] or "000000").ljust(6, "0")
        fecha = datetime.strptime(m["fecha"] + hora, "%Y-%m-%d%H%M%S")
        tipo = m["tipo"].rstrip("_-")
    else:
        fecha = mtime
        tipo = re.sub(r"\.db$", "", base)
    return Backup(nombre, tipo, fecha, tamanio, nombre.endswith(".gz"))


def planificar(backups: list[Backup], ahora: datetime) -> dict[str, str]:
    """Decide, por nombre de archivo: 'conservar', 'comprimir' o 'borrar'.
    Funcion pura (sin disco): la testea test_mantenimiento_backups.py."""
    por_tipo: dict[str, list[Backup]] = {}
    for b in backups:
        por_tipo.setdefault(b.tipo, []).append(b)

    plan = {}
    for tipo, bs in por_tipo.items():
        bs.sort(key=lambda b: b.fecha, reverse=True)
        recurrente = len({b.fecha.date() for b in bs}) >= 2
        ultimo_de_semana = {}
        for b in bs:                                   # de mas nuevo a mas viejo
            ultimo_de_semana.setdefault(b.fecha.isocalendar()[:2], b.nombre)
        for i, b in enumerate(bs):
            edad = ahora - b.fecha
            conservar = (
                edad < timedelta(hours=HORAS_GRACIA)
                or i == 0
                or (recurrente and (i < ULTIMOS_RECURRENTE or (
                    ultimo_de_semana[b.fecha.isocalendar()[:2]] == b.nombre
                    and edad <= timedelta(weeks=SEMANAS_RECURRENTE))))
                or (not recurrente and edad <= timedelta(days=DIAS_UNICO))
            )
            if not conservar:
                plan[b.nombre] = "borrar"
            elif not b.comprimido and edad >= timedelta(hours=HORAS_GRACIA):
                plan[b.nombre] = "comprimir"
            else:
                plan[b.nombre] = "conservar"
    return plan


def _comprimir(ruta: Path) -> int:
    """ruta -> ruta.gz verificado; borra el original. Devuelve el tamanio del .gz."""
    destino = ruta.with_name(ruta.name + ".gz")
    tmp = ruta.with_name(ruta.name + ".gz.tmp")
    with open(ruta, "rb") as f, gzip.open(tmp, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, length=1 << 20)
    # Verificacion: releer entero (gzip valida el CRC al final) y comparar tamanio.
    leidos = 0
    with gzip.open(tmp, "rb") as g:
        while bloque := g.read(1 << 20):
            leidos += len(bloque)
    if leidos != ruta.stat().st_size:
        tmp.unlink()
        raise RuntimeError(f"verificacion fallida: {leidos} bytes descomprimidos vs {ruta.stat().st_size}")
    shutil.copystat(ruta, tmp)
    tmp.replace(destino)
    ruta.unlink()
    return destino.stat().st_size


def ejecutar(directorio: Path, aplicar: bool, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now()
    archivos = [p for p in directorio.iterdir()
                if p.is_file() and p.name.startswith("pwos") and not p.name.endswith(".tmp")]
    backups = [clasificar(p.name, p.stat().st_size, datetime.fromtimestamp(p.stat().st_mtime)) for p in archivos]
    plan = planificar(backups, ahora)
    # Un .db restaurado con --descomprimir convive con su .gz: no se vuelve a
    # comprimir (pisaria el .gz); queda hasta que la retencion lo borre.
    for nombre, accion in plan.items():
        if accion == "comprimir" and (directorio / (nombre + ".gz")).exists():
            plan[nombre] = "conservar"
    tam = {b.nombre: b.tamanio for b in backups}

    resumen = {"aplicado": aplicar, "archivos": len(backups), "mb_antes": round(sum(tam.values()) / 1e6, 1),
               "borrar": 0, "comprimir": 0, "conservar": 0, "mb_liberados": 0.0, "errores": []}
    log = []
    for nombre in sorted(plan, key=lambda n: (plan[n], n)):
        accion = plan[nombre]
        resumen[accion] += 1
        ruta = directorio / nombre
        if accion == "conservar":
            continue
        print(f"  {accion:9s} {nombre}  ({tam[nombre] / 1e6:.1f} MB)")
        if not aplicar:
            resumen["mb_liberados"] += tam[nombre] / 1e6 if accion == "borrar" else tam[nombre] * 0.85 / 1e6
            continue
        try:
            if accion == "borrar":
                ruta.unlink()
                liberado = tam[nombre]
            else:
                liberado = tam[nombre] - _comprimir(ruta)
            resumen["mb_liberados"] += liberado / 1e6
            log.append(f"{datetime.now().isoformat(timespec='seconds')} {accion} {nombre} ({liberado / 1e6:.1f} MB liberados)")
        except Exception as e:
            resumen["errores"].append(f"{nombre}: {type(e).__name__}: {e}")
            log.append(f"{datetime.now().isoformat(timespec='seconds')} ERROR {accion} {nombre}: {e}")

    resumen["mb_liberados"] = round(resumen["mb_liberados"], 1)
    resumen["mb_despues"] = round(resumen["mb_antes"] - resumen["mb_liberados"], 1)
    if aplicar and log:
        ruta_log = directorio / LOG
        previas = ruta_log.read_text(encoding="utf-8").splitlines() if ruta_log.exists() else []
        ruta_log.write_text("\n".join((previas + log)[-2000:]) + "\n", encoding="utf-8")
    return resumen


def descomprimir(ruta_gz: Path) -> Path:
    if not ruta_gz.name.endswith(".gz"):
        raise SystemExit("--descomprimir espera un archivo .gz")
    destino = ruta_gz.with_name(ruta_gz.name[:-3])
    if destino.exists():
        raise SystemExit(f"ya existe {destino}, no se pisa")
    with gzip.open(ruta_gz, "rb") as g, open(destino, "wb") as f:
        shutil.copyfileobj(g, f, length=1 << 20)
    return destino


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=BACKUPS_DIR)
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--descomprimir", type=Path)
    a = ap.parse_args()
    if a.descomprimir:
        print(f"Restaurado en: {descomprimir(a.descomprimir)} (pwos.db no se toco)")
        return
    print(f"Backups en {a.dir} -- {'APLICANDO' if a.aplicar else 'SIMULACION (nada se modifica; usar --aplicar)'}")
    r = ejecutar(a.dir, a.aplicar)
    print(f"\n{r['archivos']} archivos, {r['mb_antes']} MB: {r['borrar']} a borrar, {r['comprimir']} a comprimir, "
          f"{r['conservar']} sin cambios. {'Liberados' if a.aplicar else 'Se liberarian ~'} {r['mb_liberados']} MB "
          f"(quedan ~{r['mb_despues']} MB). Errores: {len(r['errores'])}")
    for e in r["errores"]:
        print("  ERROR", e)
    print("RESUMEN_JSON: " + json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
