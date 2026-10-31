"""
Casos de prueba de mantenimiento_backups.py. La logica de decision
(`planificar`) sin disco; la compresion/borrado en una carpeta temporal.

Uso:
    python test_mantenimiento_backups.py
"""
import gzip
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import mantenimiento_backups as mb

AHORA = datetime(2026, 9, 30, 20, 0)


def b(nombre, dias_atras, tipo=None, comprimido=False, horas=0):
    fecha = AHORA - timedelta(days=dias_atras, hours=horas)
    return mb.Backup(nombre, tipo or nombre.split("_2")[0], fecha, 1000, comprimido)


def test_clasificar_nombres_reales():
    x = mb.clasificar("pwos_backup_pre-indicadores-macro_2026-09-30_184012.db", 1, AHORA)
    assert (x.tipo, x.fecha, x.comprimido) == ("pwos_backup_pre-indicadores-macro", datetime(2026, 9, 30, 18, 40, 12), False)
    x = mb.clasificar("pwos.db.backup_migrar_v239_2026-09-29_184839.gz", 1, AHORA)
    assert x.tipo == "pwos.db.backup_migrar_v239" and x.comprimido
    x = mb.clasificar("pwos_backup_pre-correccion-pf-billetera-2026-09-06.db", 1, AHORA)
    assert x.tipo == "pwos_backup_pre-correccion-pf-billetera" and x.fecha == datetime(2026, 9, 6)
    x = mb.clasificar("pwos_backup_pre-banco-historico.db", 1, AHORA)     # sin fecha: usa mtime
    assert x.tipo == "pwos_backup_pre-banco-historico" and x.fecha == AHORA


def test_recurrente_7_ultimos_mas_1_por_semana():
    # 1 backup diario durante 70 dias del mismo tipo
    bs = [b(f"pre-precios_2026-{i:03d}", i, tipo="pre-precios") for i in range(70)]
    plan = mb.planificar(bs, AHORA)
    conservados = [x for x in bs if plan[x.nombre] != "borrar"]
    # 7 ultimos + ultimos de cada semana ISO de las 8 semanas (algunas se solapan con los 7)
    assert all(plan[x.nombre] != "borrar" for x in bs[:7])
    semanas = {x.fecha.isocalendar()[:2] for x in conservados if (AHORA - x.fecha).days > 6}
    assert all(sum(1 for x in conservados if x.fecha.isocalendar()[:2] == s and (AHORA - x.fecha).days > 6) == 1
               for s in semanas), "1 solo por semana fuera de los 7 ultimos"
    assert all(plan[x.nombre] == "borrar" for x in bs if (AHORA - x.fecha) > timedelta(weeks=8))
    assert 12 <= len(conservados) <= 16, len(conservados)


def test_unico_180_dias_y_siempre_el_ultimo_de_cada_tipo():
    bs = [b("migrar_v200_x", 200, tipo="migrar_v200"),        # unico y viejo, pero es el unico de su tipo
          b("migrar_v210_x", 100, tipo="migrar_v210")]
    plan = mb.planificar(bs, AHORA)
    assert plan["migrar_v200_x"] == "comprimir"                # nunca se queda un tipo sin backup
    assert plan["migrar_v210_x"] == "comprimir"
    bs = [b("corr_a", 200, tipo="corr"), b("corr_b", 200, tipo="corr", horas=1)]   # mismo dia -> unico
    plan = mb.planificar(bs, AHORA)
    assert sorted(plan.values()) == ["borrar", "comprimir"]    # el mas reciente se conserva


def test_menos_de_24h_no_se_toca():
    bs = [b("pre-x_hoy", 0, tipo="pre-x", horas=3), b("pre-x_ayer", 1, tipo="pre-x", horas=2)]
    plan = mb.planificar(bs, AHORA)
    assert plan["pre-x_hoy"] == "conservar"                    # ni se comprime
    assert plan["pre-x_ayer"] == "comprimir"


def test_ya_comprimido_no_se_recomprime():
    plan = mb.planificar([b("pre-y_a.gz", 3, tipo="pre-y", comprimido=True)], AHORA)
    assert plan["pre-y_a.gz"] == "conservar"


def test_aplicar_en_carpeta_temporal():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        def base(nombre, filas):
            con = sqlite3.connect(d / nombre)
            con.execute("CREATE TABLE t (x TEXT)")
            con.executemany("INSERT INTO t VALUES (?)", [("dato repetido " * 20,)] * filas)
            con.commit()
            con.close()
        base("pwos_backup_pre-z_2026-09-20_100000.db", 2000)   # recurrente, dentro de los 7: comprimir
        base("pwos_backup_pre-z_2026-09-30_190000.db", 2000)   # < 24 h: conservar
        base("pwos_backup_pre-w_2026-01-01_100000.db", 10)     # unico viejo pero ultimo de su tipo: comprimir
        base("pwos_backup_pre-w_2026-01-01_090000.db", 10)     # unico, > 180 dias, no es el ultimo: borrar
        (d / "notas.txt").write_text("no es un backup")          # se ignora

        sim = mb.ejecutar(d, aplicar=False, ahora=AHORA)
        assert (sim["borrar"], sim["comprimir"], sim["conservar"]) == (1, 2, 1), sim
        assert len(list(d.iterdir())) == 5                      # la simulacion no toca nada

        r = mb.ejecutar(d, aplicar=True, ahora=AHORA)
        assert not r["errores"], r["errores"]
        nombres = sorted(p.name for p in d.iterdir())
        assert nombres == ["mantenimiento_backups.log", "notas.txt",
                           "pwos_backup_pre-w_2026-01-01_100000.db.gz",
                           "pwos_backup_pre-z_2026-09-20_100000.db.gz",
                           "pwos_backup_pre-z_2026-09-30_190000.db"], nombres
        # el .gz se restaura a una base valida y con los mismos datos
        restaurado = mb.descomprimir(d / "pwos_backup_pre-z_2026-09-20_100000.db.gz")
        con = sqlite3.connect(restaurado)
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2000
        con.close()
        assert r["mb_liberados"] > 0
        # segunda corrida, con el .db restaurado al lado de su .gz: no se
        # recomprime (pisaria el .gz) y no hay nada mas que hacer
        r2 = mb.ejecutar(d, aplicar=True, ahora=AHORA)
        assert (r2["borrar"], r2["comprimir"]) == (0, 0), r2
        assert (d / "pwos_backup_pre-z_2026-09-20_100000.db.gz").exists() and restaurado.exists()


def test_gzip_corrupto_no_borra_el_original():
    with tempfile.TemporaryDirectory() as d:
        ruta = Path(d) / "pwos_backup_pre-q_2026-09-01_100000.db"
        ruta.write_bytes(b"x" * 10000)
        original = gzip.open
        # simula un .gz que al releerlo da menos bytes
        class Corto:
            def __init__(self, *a, **k): self.f = original(*a, **k); self.modo = a[1] if len(a) > 1 else k.get("mode", "rb")
            def __enter__(self): self.f.__enter__(); return self
            def __exit__(self, *a): return self.f.__exit__(*a)
            def write(self, x): return self.f.write(x)
            def read(self, n=-1):
                datos = self.f.read(n)
                return datos[:-1] if datos else datos
        mb.gzip.open = Corto
        try:
            try:
                mb._comprimir(ruta)
            except RuntimeError:
                pass
            else:
                raise AssertionError("deberia fallar la verificacion")
        finally:
            mb.gzip.open = original
        assert ruta.exists() and not ruta.with_name(ruta.name + ".gz").exists()


def main():
    pruebas = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for p in pruebas:
        p()
        print(f"  OK  {p.__name__}")
    print(f"\nOK -- {len(pruebas)} casos de prueba de mantenimiento_backups.py")


if __name__ == "__main__":
    main()
