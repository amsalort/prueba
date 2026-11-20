"""Utilidades generales: entorno del sistema, rutas, formato de tamaños, procesos externos."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Variables que PyInstaller / AppRun modifican y que no deben heredar los
# programas del sistema que lanzamos (gsettings, xdg-open, GIMP, terminal...).
_PRIVATE_VARS = ("LD_LIBRARY_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                 "PYTHONHOME", "PYTHONPATH", "QML2_IMPORT_PATH", "SSL_CERT_FILE",
                 "GDK_PIXBUF_MODULE_FILE", "GIO_MODULE_DIR", "GTK_PATH", "XDG_DATA_DIRS")


def host_env() -> dict:
    """Entorno limpio para ejecutar programas del sistema anfitrión."""
    env = dict(os.environ)
    for var in _PRIVATE_VARS:
        orig = env.pop(var + "_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif var in env and var != "XDG_DATA_DIRS":
            env.pop(var)
    for k in list(env):
        if k.startswith("RETROVISOR_ORIG_"):
            env[k[len("RETROVISOR_ORIG_"):]] = env.pop(k)
    return env


def run_host(args, **kw):
    """Ejecuta un programa del sistema de forma síncrona."""
    return subprocess.run(args, env=host_env(), capture_output=True, text=True, **kw)


def spawn_host(args, cwd=None) -> bool:
    """Lanza un programa del sistema en segundo plano. Devuelve False si no existe."""
    if not args or shutil.which(args[0]) is None:
        return False
    try:
        subprocess.Popen(args, env=host_env(), cwd=cwd, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def resource_dir() -> Path:
    """Carpeta donde están los binarios auxiliares (cjpeg, jpegtran) e iconos."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def find_helper(name: str) -> str | None:
    """Busca un binario auxiliar empaquetado (p. ej. cjpeg de MozJPEG)."""
    env = os.environ.get("RETROVISOR_" + name.upper())
    if env and os.access(env, os.X_OK):
        return env
    candidates = [resource_dir() / "bin" / name]
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(Path(appdir) / "usr" / "bin" / name)
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "retrovisor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    p = Path(base) / "retrovisor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "retrovisor"
    p.mkdir(parents=True, exist_ok=True)
    return p


def human_size(n: int | float) -> str:
    """Formato al estilo del explorador clásico: '12 KB', '1,5 MB'."""
    if n < 1024:
        return f"{int(n)} bytes"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            if unit == "KB":
                return f"{int(round(n)):,} KB".replace(",", ".")
            return f"{n:,.1f} {unit}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{n} bytes"


def kb_size(n: int) -> str:
    kb = (n + 1023) // 1024
    return f"{kb:,} KB".replace(",", ".")


def unique_path(path: Path) -> Path:
    """Devuelve una ruta libre añadiendo ' (2)', ' (3)'..."""
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not cand.exists():
            return cand
        i += 1
