"""Prepara una carpeta de prueba para la validación completa de mANY-MAZE con computer use.

    .venv/bin/python qa/herramientas/preparar_prueba.py                 # ~/mANY-MAZE-QA/<fecha-hora>
    .venv/bin/python qa/herramientas/preparar_prueba.py --dest DIR --segundos 60 --sin-videos-reales

Crea:
  entradas/demo_original/        experimento de demostración (no tocar: es la copia limpia)
  entradas/videos_reales/        recortes de los videos reales de ~/Downloads/Ratas (OFT, EPM, MWM, FST)
  entradas/videos_limite/        videos vacíos, corruptos, truncados, de 1 s, con cuadros negros / repetidos, partes
                                 para unir, nombres con acentos y espacios, un texto disfrazado de video, una lista M3U
                                 con un archivo que falta
  entradas/importar/             animales (CSV ANY-maze, Excel, Windows-1252, con problemas, vacío), programa de
                                 pruebas, track en texto, track de DeepLabCut, XML de ANY-maze (bueno, real, roto),
                                 mapas de zonas de ANY-maze
  entradas/solo_lectura/         carpeta sin permiso de escritura (para guardar / exportar ahí)
  referencia/                    resultados del demo calculados por la línea de comandos (el oráculo para comparar
                                 lo que exporte la interfaz)
  evidencias/                    capturas de pantalla
  bitacora/                      la bitácora y los hallazgos de esta ejecución (copiados de qa/plantillas)
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
RATAS = Path.home() / "Downloads" / "Ratas"
REALES = {  # nombre del recorte → (carpeta, índice del video ordenado por nombre)
    "OFT_macho_1": ("Videos_Pruebas Cognitivas_LM-A_F0/OFT_LM-A_F0_22.04.2025/OFT_LM-A_F0_MACHOS", 0),
    "OFT_macho_2": ("Videos_Pruebas Cognitivas_LM-A_F0/OFT_LM-A_F0_22.04.2025/OFT_LM-A_F0_MACHOS", 1),
    "OFT_hembra_1": ("Videos_Pruebas Cognitivas_LM-A_F0/OFT_LM-A_F0_22.04.2025/OFT_LM-A_F0_HEMBRAS", 0),
    "EPM_macho_1": ("Videos_Pruebas Cognitivas_LM-A_F0/EPM_LM-A_F0_23.04.2025/EPM_LM-A_F0_MACHOS", 0),
    "EPM_hembra_1": ("Videos_Pruebas Cognitivas_LM-A_F0/EPM_LM-A_F0_23.04.2025/EPM_LM-A_F0_HEMBRAS", 0),
    "MWM_hembra_1": ("MWM/MWM_T1-T8_LM-A_F0_HEMBRAS_29.04.2025", 0),
    "MWM_hembra_2": ("MWM/MWM_T1-T8_LM-A_F0_HEMBRAS_29.04.2025", 1),
    "MWM_hembra_3": ("MWM/MWM_T1-T8_LM-A_F0_HEMBRAS_29.04.2025", 2),
    "FST_hembra_1": ("Videos_Pruebas Cognitivas_LM-A_F0/FST_LM-A_F0_HEMBRAS_05.06.2025", 0),
    "FST_hembra_2": ("Videos_Pruebas Cognitivas_LM-A_F0/FST_LM-A_F0_HEMBRAS_05.06.2025", 1),
}


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------------------------------- videos
def write_video(path: Path, frames, fps: float, size: tuple[int, int]):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    n = 0
    for f in frames:
        w.write(f)
        n += 1
    w.release()
    return n


def cut(src: Path, dst: Path, seconds: float, half: bool = True):
    """Los primeros `seconds` segundos de `src`, a mitad de resolución (más rápido de rastrear), en MP4."""
    cap = cv2.VideoCapture(str(src))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    size = (w // 2, h // 2) if half else (w, h)
    limit = int(round(seconds * fps))

    def frames():
        for _ in range(limit):
            ok, f = cap.read()
            if not ok:
                return
            yield cv2.resize(f, size, interpolation=cv2.INTER_AREA) if half else f

    n = write_video(dst, frames(), fps, size)
    cap.release()
    return n, fps, size


def synthetic_frames(n, size=(320, 240), seed=0, black=(), repeat=()):
    """Un punto oscuro que da vueltas sobre fondo claro; `black` cuadros negros, `repeat` cuadros repetidos."""
    rng = np.random.default_rng(seed)
    prev = None
    for i in range(n):
        if i in repeat and prev is not None:
            yield prev
            continue
        f = np.full((size[1], size[0], 3), 200, np.uint8)
        if i in black:
            f[:] = 0
        else:
            cx = int(size[0] / 2 + size[0] / 3 * np.cos(i / 15))
            cy = int(size[1] / 2 + size[1] / 3 * np.sin(i / 15))
            cv2.ellipse(f, (cx, cy), (14, 8), (i * 6) % 360, 0, 360, (30, 30, 30), -1)
            f = np.clip(f.astype(int) + rng.integers(-4, 5, f.shape), 0, 255).astype(np.uint8)
        prev = f
        yield f


def edge_videos(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    fps = 25
    write_video(d / "valido_10s.mp4", synthetic_frames(250), fps, (320, 240))
    write_video(d / "muy_corto_1s.mp4", synthetic_frames(25), fps, (320, 240))
    write_video(d / "un_solo_cuadro.mp4", synthetic_frames(1), fps, (320, 240))
    write_video(d / "nombre con espacios y acentos ñáé ü.mp4", synthetic_frames(125), fps, (320, 240))
    write_video(d / "vertical_240x320.mp4", synthetic_frames(125, size=(240, 320)), fps, (240, 320))
    write_video(d / "fallos_cuadros_negros_y_repetidos.mp4",
                synthetic_frames(250, black={50, 51, 52, 180}, repeat={100, 101, 102, 103, 200}), fps, (320, 240))
    write_video(d / "parte_1.mp4", synthetic_frames(125, seed=1), fps, (320, 240))
    write_video(d / "parte_2.mp4", synthetic_frames(125, seed=2), fps, (320, 240))
    write_video(d / "parte_3.mp4", synthetic_frames(125, seed=3), fps, (320, 240))
    (d / "vacio_0_bytes.mp4").write_bytes(b"")
    (d / "corrupto_bytes_aleatorios.mp4").write_bytes(random.Random(0).randbytes(200_000))
    good = (d / "valido_10s.mp4").read_bytes()
    (d / "truncado_30pc.mp4").write_bytes(good[: len(good) * 3 // 10])
    (d / "texto_disfrazado.mp4").write_text("esto no es un video\n" * 100)
    (d / "lista_con_archivo_que_falta.m3u").write_text("parte_1.mp4\nno_existe.mp4\nparte_2.mp4\n")
    (d / "lista_buena.m3u").write_text("parte_1.mp4\nparte_2.mp4\nparte_3.mp4\n")


# ---------------------------------------------------------------------------------------------------- imports
def import_files(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    (d / "animales_anymaze.csv").write_text(
        "Animal,Animal ID,Status,Treatment,Animal weight,Sex,Notes\n"
        + "".join(f"{i},R{i:02d},Active,{'Control' if i % 2 else 'HFD'},{250 + i * 3},"
                  f"{'Male' if i <= 6 else 'Female'},nota {i}\n" for i in range(1, 13)), encoding="utf-8")
    (d / "animales_windows1252.csv").write_text(
        "Animal ID;Treatment;Sex;Notes\nÑ01;Dieta alta en grasa;Hembra;Señal débil, oreja izquierda\n"
        "Á02;Control;Macho;Pérdida de peso\n", encoding="cp1252")
    (d / "animales_con_problemas.csv").write_text(
        "Animal ID,Treatment,Sex,Peso,Columna extra\n"
        "P01,Control,Male,250,x\n"
        "P01,Control,Male,251,duplicado\n"          # ID repetido
        ",HFD,Female,240,sin ID\n"                    # sin ID
        "P03,,Unknown,abc,peso no numérico\n"         # sin tratamiento, sexo raro, peso texto
        "\n"                                          # fila vacía
        "P04,Control,F,-5,peso negativo\n"
        "P05,\"Con, coma\",M,1e9,peso enorme\n"
        + "P06," + "X" * 300 + ",Male,250,tratamiento de 300 caracteres\n"
        "P07,Control;Mal separador,Male,250,\n", encoding="utf-8")
    (d / "animales_solo_encabezado.csv").write_text("Animal ID,Treatment,Sex\n", encoding="utf-8")
    (d / "animales_vacio_0_bytes.csv").write_bytes(b"")
    (d / "animales_1000.csv").write_text(
        "Animal ID,Treatment,Sex\n" + "".join(f"M{i:04d},{['A', 'B', 'C', 'D'][i % 4]},{'Male' if i % 2 else 'Female'}\n"
                                              for i in range(1000)), encoding="utf-8")
    try:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Animal ID", "Treatment", "Sex", "Weight (g)"])
        for i in range(1, 7):
            ws.append([f"X{i}", "Saline" if i % 2 else "Drug", "Male" if i < 4 else "Female", 20 + i])
        wb.save(d / "animales.xlsx")
    except ImportError:
        log("  (sin openpyxl: no se creó animales.xlsx)")
    (d / "programa_pruebas.csv").write_text(
        "Animal,Stage,Trial,Apparatus,Video\n"
        + "".join(f"R{i:02d},Day 1,1,Open field,\n" for i in range(1, 7)), encoding="utf-8")
    lines = ["Time\tCentre position X\tCentre position Y\tHead position X\tHead position Y"]
    for i in range(500):
        lines.append(f"{i * 0.04:.2f}\t{0.1 + i * 0.0004:.4f}\t0.2\t{0.12 + i * 0.0004:.4f}\t0.2")
    (d / "track_tabulado_metros.txt").write_text("\n".join(lines), encoding="utf-8")
    # DeepLabCut: tres filas de encabezado (scorer / bodyparts / coords)
    parts = ["nose", "mid", "tailbase"]
    rows = ["scorer," + ",".join(["DLC_resnet50"] * 9), "bodyparts," + ",".join(p for p in parts for _ in range(3)),
            "coords," + ",".join(c for _ in parts for c in ("x", "y", "likelihood"))]
    for i in range(250):
        x = 160 + 100 * np.cos(i / 15)
        y = 120 + 80 * np.sin(i / 15)
        vals = []
        for k, off in enumerate((12, 0, -12)):
            vals += [f"{x + off:.2f}", f"{y:.2f}", f"{0.95 if i % 40 else 0.2:.2f}"]
        rows.append(f"{i}," + ",".join(vals))
    (d / "track_deeplabcut.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    from tests.test_results_distribution import AM_XML, _real_layout_xml, _write_map

    (d / "anymaze_experimento.xml").write_text(AM_XML, encoding="utf-8")
    (d / "anymaze_formato_real.xml").write_text(_real_layout_xml(), encoding="cp1252")
    (d / "anymaze_roto.xml").write_text(AM_XML[: len(AM_XML) // 2], encoding="utf-8")
    (d / "xml_que_no_es_anymaze.xml").write_text("<?xml version='1.0'?><root><a>1</a></root>", encoding="utf-8")
    zm = d / "mapas_de_zonas"
    zm.mkdir(exist_ok=True)
    h, w = 60, 80
    filled = np.zeros((h, w), int)
    filled[10:30, 20:50] = 1
    _write_map(zm / "Individual zone area map for Box A, Centre zone.csv",
               [["Zone area map for", "Box A", "Centre"], ["Zone area map dimensions (w x h)", w, h]], filled)
    combined = np.zeros((h, w), int)
    combined[:, :40] = 1
    combined[:, 40:] = 2
    _write_map(zm / "Combined zone area map for Box B.csv",
               [["Zone names and codes:"], ["Left", 1], ["Right", 2], ["Zone area map dimensions (w x h)", w, h]],
               combined)
    (zm / "mapa_roto.csv").write_text("Zone area map for,Box C\nbasura,,,\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- referencia real
def independent_reference(dest: Path):
    """Las filas del análisis independiente (~/Downloads/Ratas/analisis_conductual) de los videos originales usados,
    para comparar con lo que mida mANY-MAZE sobre el video COMPLETO (no sobre el recorte)."""
    import csv

    src = RATAS / "analisis_conductual"
    stems = {}
    for name, (folder, idx) in REALES.items():
        vids = sorted((RATAS / folder).glob("*.mov")) if (RATAS / folder).is_dir() else []
        if idx < len(vids):
            stems[vids[idx].stem] = (name, vids[idx])
    out = dest / "referencia" / "analisis_independiente"
    out.mkdir(parents=True, exist_ok=True)
    for f in ("OFT_01_por_animal.csv", "EPM_01_por_animal.csv", "MWM_01_por_ensayo.csv", "FST_01_por_animal.csv"):
        if not (src / f).exists():
            continue
        with (src / f).open(encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        keep = [r for r in rows if r.get("archivo") in stems]
        if keep:
            with (out / f).open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=["recorte", "video_original"] + list(keep[0].keys()))
                w.writeheader()
                for r in keep:
                    name, path = stems[r["archivo"]]
                    w.writerow({"recorte": name, "video_original": str(path), **r})
    (out / "LEEME.txt").write_text(
        "Filas del análisis independiente previo (su propio detector, 10 fps, suavizado) para los videos originales\n"
        "de los que salen los recortes. Son una REFERENCIA, no la verdad: MWM bien validado; inmovilidad de FST dudosa.\n"
        "Compara contra mANY-MAZE rastreando el video ORIGINAL completo (5 min), no el recorte de 60 s.\n"
        "OFT: dos animales por video (columna lado = izq/der): hace falta un aparato por lado.\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- oráculos
def oracles(dest: Path):
    """Valores esperados calculados de forma independiente de la app (para comparar con lo que muestre)."""
    out = []
    # 1. Check video: cuadros negros y repetidos, desde los cuadros decodificados
    vid = dest / "entradas" / "videos_limite" / "fallos_cuadros_negros_y_repetidos.mp4"
    cap = cv2.VideoCapture(str(vid))
    prev, black, dup, i = None, [], [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = f.astype(np.int16)
        if g.mean() < 16 and g.std() < 8:
            black.append(i)
        elif prev is not None and np.abs(g - prev).mean() < 0.05:
            dup.append(i)
        prev, i = g, i + 1
    out += ["== Check video: fallos_cuadros_negros_y_repetidos.mp4",
            f"cuadros: {i}", f"negros (contando desde 0): {black}",
            f"repetidos (iguales al anterior tras decodificar; el MP4 comprimido puede alterar alguno): {dup}",
            "faltantes (huecos en las marcas de tiempo): ninguno", ""]
    # 2. Estadística del demo: Centre: time (%) por grupo
    import csv

    from scipy import stats

    rows = list(csv.DictReader((dest / "referencia" / "demo_resultados.csv").open(encoding="utf-8-sig")))
    col = "Centre: time (%)"
    groups: dict[str, list[float]] = {}
    for r in rows:
        groups.setdefault(r["Group"], []).append(float(r[col]))
    out.append(f"== Estadística del demo: «{col}» por Group")
    for g, v in groups.items():
        out.append(f"{g}: n={len(v)} media={np.mean(v):.4f} DE={np.std(v, ddof=1):.4f} EEM={stats.sem(v):.4f}")
    if len(groups) == 2:
        a, b = groups.values()
        t = stats.ttest_ind(a, b, equal_var=False)
        u = stats.mannwhitneyu(a, b, alternative="two-sided")
        tt = stats.ttest_ind(a, b, equal_var=True)
        out += [f"Welch t: t={t.statistic:.4f} p={t.pvalue:.6f}", f"t de Student: t={tt.statistic:.4f} p={tt.pvalue:.6f}",
                f"Mann-Whitney U: U={u.statistic:.1f} p={u.pvalue:.6f}"]
    col2 = "Total distance (cm)"
    groups2: dict[str, list[float]] = {}
    for r in rows:
        groups2.setdefault(r["Group"], []).append(float(r[col2]))
    out.append(f"«{col2}» por Group: " + "; ".join(f"{g}: media={np.mean(v):.3f}" for g, v in groups2.items()))
    x = [float(r[col]) for r in rows]
    y = [float(r[col2]) for r in rows]
    pr = stats.pearsonr(x, y)
    out += [f"Correlación de Pearson «{col}» vs «{col2}»: r={pr.statistic:.4f} p={pr.pvalue:.6f}", ""]
    # 3. Calculadora de dosis: volumen (mL) = peso (g) / 1000 × dosis (mg/kg) / concentración (mg/mL)
    out += ["== Calculadora de dosis (dosis 10 mg/kg, concentración 2 mg/mL)",
            "peso 25 g → 0.125 mL;  peso 250 g → 1.25 mL;  peso 0 g → 0 mL;  concentración 0 → debe avisar", ""]
    # 4. Variabilidad de los criterios de entrenamiento (ejemplo de la guía)
    out += ["== Criterio de entrenamiento: variabilidad ANY-maze de 25, 24, 27 = 5.9 %", ""]
    (dest / "referencia" / "ORACULOS.txt").write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------- versión anterior
def old_version(out: Path, tag: str = "v0.2.0"):
    """Un experimento de demostración creado por una versión anterior de la app (git worktree del tag), para
    comprobar que la versión actual lo abre, lo convierte y da los mismos resultados."""
    import tempfile

    wt = Path(tempfile.mkdtemp()) / "mm_old"
    r = subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), tag],
                       capture_output=True, text=True)
    if r.returncode:
        log(f"  no se pudo preparar {tag}: {r.stderr.strip()[-300:]}")
        return
    try:
        out.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(wt))
        r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "manymaze", "demo",
                            str(out / f"Demo {tag}.mmaze")], capture_output=True, text=True, env=env, cwd=str(wt))
        if r.returncode:
            log(f"  {tag} no pudo crear el demo: {r.stderr.strip()[-300:]}")
            return
        r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "manymaze", "project",
                            str(out / f"Demo {tag}.mmaze"), "results", "-o", str(out / f"resultados_{tag}.csv")],
                           capture_output=True, text=True, env=env, cwd=str(wt))
        (out / "LEEME.txt").write_text(
            f"«Demo {tag}.mmaze» lo creó la versión {tag}; resultados_{tag}.csv son sus resultados según esa versión.\n"
            "Ábrelo con la versión actual (sobre una COPIA), exporta CSV y compáralo con comparar_con_referencia.py:\n"
            "las columnas comunes deben coincidir salvo cambios documentados entre versiones.\n", encoding="utf-8")
    finally:
        subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True)


def b06_experiments(dest: Path, demo: Path):
    """Los experimentos que B06/B07 dan por hechos: `programa.mmaze` (copia del demo) y `videos.mmaze` (nuevo, sin
    pruebas: aparato de campo abierto sobre la arena izquierda de OFT_macho_1.mp4, animales R1–R3, etapas Día 1 y
    Día 2), para que B06-01 empiece añadiendo los videos."""
    from manymaze.core import templates
    from manymaze.core.project import Animal, Project

    exp = dest / "experimentos"
    shutil.copytree(demo, exp / "programa.mmaze")
    clip = dest / "entradas" / "videos_reales" / "OFT_macho_1.mp4"
    proj = Project(name="Videos B06", protocol="open_field", test_duration_s=0)
    proj.path = exp / "videos.mmaze"
    proj.path.mkdir(parents=True, exist_ok=True)
    size = (640, 360)
    if clip.exists():
        cap = cv2.VideoCapture(str(clip))
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        cap.release()
    # la arena izquierda (los OFT tienen dos campos abiertos lado a lado), en la mitad de resolución de los recortes
    app = templates.build("open_field", 65, 40, 262, 260, size_cm=40)
    app.frame_size = size
    proj.apparatus.append(app)
    proj.animals = [Animal(f"R{i}") for i in (1, 2, 3)]
    proj.stages = ["Día 1", "Día 2"]
    proj.save()


def app_version() -> str:
    """Versión y fecha de dist/mANY-MAZE.app (la que se prueba), o «no construida»."""
    plist = ROOT / "dist" / "mANY-MAZE.app" / "Contents" / "Info.plist"
    if not plist.exists():
        return "no construida (dist/mANY-MAZE.app no existe: ver §3.2)"
    import plistlib

    info = plistlib.loads(plist.read_bytes())
    built = dt.datetime.fromtimestamp(plist.stat().st_mtime).isoformat(timespec="minutes")
    return f"{info.get('CFBundleShortVersionString', '?')} (construida {built})"


def code_version() -> str:
    from manymaze import __version__

    when = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cd", "--date=iso"], capture_output=True,
                          text=True).stdout.strip()
    return f"{__version__} (último commit {when})"

# ---------------------------------------------------------------------------------------------------- main
def manymaze(*args, cwd=None):
    exe = ROOT / ".venv" / "bin" / "manymaze"
    r = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    if r.returncode:
        log("  ERROR:", " ".join(map(str, args)), r.stderr[-800:])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path,
                    default=Path.home() / "mANY-MAZE-QA" / dt.datetime.now().strftime("%Y-%m-%d_%H%M"))
    ap.add_argument("--segundos", type=float, default=60, help="duración de los recortes de videos reales")
    ap.add_argument("--sin-videos-reales", action="store_true")
    a = ap.parse_args()
    dest: Path = a.dest.expanduser()
    if dest.exists() and any(dest.iterdir()):
        sys.exit(f"{dest} ya existe y no está vacía: elige otra --dest")
    ent = dest / "entradas"
    for sub in ("evidencias", "bitacora", "referencia", "salidas", "experimentos"):
        (dest / sub).mkdir(parents=True, exist_ok=True)

    log("1/8 Experimento de demostración…")
    from manymaze.core.demo import create_demo_project

    demo = ent / "demo_original" / "Demo open field.mmaze"
    create_demo_project(demo)

    log("2/8 Resultados de referencia por línea de comandos…")
    ref = dest / "referencia"
    manymaze("project", demo, "info")
    manymaze("project", demo, "results", "-o", ref / "demo_resultados.csv")
    manymaze("project", demo, "results", "-o", ref / "demo_resultados.xlsx")
    manymaze("project", demo, "results", "-o", ref / "demo_resultados_por_intervalos.csv", "--bins")
    manymaze("project", demo, "report", "-o", ref / "demo_reporte.html")
    r = subprocess.run([str(ROOT / ".venv" / "bin" / "manymaze"), "project", str(demo), "info"],
                       capture_output=True, text=True)
    (ref / "demo_info.txt").write_text(r.stdout + r.stderr)

    log("3/8 Videos para casos límite…")
    edge_videos(ent / "videos_limite")

    log("4/8 Archivos para importar…")
    import_files(ent / "importar")

    if not a.sin_videos_reales:
        log(f"5/8 Recortes de {a.segundos:.0f} s de los videos reales…")
        out = ent / "videos_reales"
        out.mkdir(parents=True, exist_ok=True)
        lines = []
        for name, (folder, idx) in REALES.items():
            src_dir = RATAS / folder
            vids = sorted(p for p in src_dir.glob("*.mov")) if src_dir.is_dir() else []
            if idx >= len(vids):
                log(f"  falta {src_dir} — se omite {name}")
                continue
            n, fps, size = cut(vids[idx], out / f"{name}.mp4", a.segundos)
            lines.append(f"{name}.mp4\t{n} cuadros\t{fps:.0f} fps\t{size[0]}×{size[1]}\toriginal: {vids[idx]}")
            log("  ", lines[-1])
        (out / "ORIGENES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        independent_reference(dest)
    else:
        log("5/8 (sin videos reales)")

    b06_experiments(dest, demo)
    oracles(dest)
    log("6/8 Oráculos de verdad conocida (34 experimentos) y de estadística (72 pruebas)…")
    orc = dest / "oraculos"
    py = str(ROOT / ".venv" / "bin" / "python")
    for script, sub in (("oraculo_sintetico.py", "trayectorias"), ("oraculo_estadistica.py", "estadistica")):
        r = subprocess.run([py, str(ROOT / "qa" / "herramientas" / script), "generar", str(orc / sub)],
                           capture_output=True, text=True)
        if r.returncode:
            log("  ERROR:", script, r.stderr[-800:])
    # calibración de la capa A (sobre una copia: el oráculo queda sin rastrear para la prueba en la app)
    r = subprocess.run([py, str(ROOT / "qa" / "herramientas" / "oraculo_sintetico.py"), "calibrar",
                        str(orc / "trayectorias")], capture_output=True, text=True)
    if r.returncode:
        log("  ERROR calibrando:", r.stderr[-800:])
    log("7/8 Experimento creado con la versión v0.2.0 (compatibilidad con versiones anteriores)…")
    old_version(ent / "version_anterior")
    log("8/8 Carpeta de solo lectura y plantillas de bitácora…")
    ro = ent / "solo_lectura"
    ro.mkdir(parents=True, exist_ok=True)
    (ro / "LEEME.txt").write_text("Carpeta sin permiso de escritura: guardar o exportar aquí debe dar un error claro.\n")
    ro.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
    for f in (ROOT / "qa" / "plantillas").glob("*.md"):
        shutil.copy(f, dest / "bitacora" / f.name)
    # los hallazgos ya conocidos, al final de HALLAZGOS.md bajo «Hallazgos previos» (para no duplicarlos); el
    # H-001 de la plantilla es un ejemplo: el primer hallazgo de la ejecución lo sustituye
    prev = (ROOT / "qa" / "HALLAZGOS_PREVIOS.md").read_text(encoding="utf-8")
    h = dest / "bitacora" / "HALLAZGOS.md"
    h.write_text(h.read_text(encoding="utf-8") + "\n---\n\n" + prev.replace("# Hallazgos previos", "## Hallazgos previos"),
                 encoding="utf-8")
    if (ROOT / "qa" / "errores_al_pulsar.txt").exists():
        shutil.copy(ROOT / "qa" / "errores_al_pulsar.txt", dest / "bitacora" / "errores_al_pulsar.txt")
    shutil.copy(ROOT / "qa" / "INVENTARIO_UI.md", dest / "bitacora" / "INVENTARIO_UI.md")
    (dest / "bitacora" / "ENTORNO.txt").write_text(
        f"fecha: {dt.datetime.now().isoformat(timespec='seconds')}\n"
        f"commit: {subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()}\n"
        f"macOS: {subprocess.run(['sw_vers', '-productVersion'], capture_output=True, text=True).stdout.strip()}\n"
        f"versión de la app empaquetada: {app_version()}\n"
        f"versión del código: {code_version()}\n"
        f"carpeta: {dest}\n", encoding="utf-8")
    log(f"\nListo: {dest}")


if __name__ == "__main__":
    main()
