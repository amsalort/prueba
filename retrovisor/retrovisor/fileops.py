"""Operaciones de archivos: papelera, copiar/mover, portapapeles, descripciones, fondo de escritorio."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from PySide6.QtCore import QByteArray, QFile, QMimeData, QUrl
from PySide6.QtGui import QGuiApplication

from .settings import settings
from .util import data_dir, run_host, spawn_host, unique_path


# ------------------------------------------------------------------ papelera / borrar

def delete_path(path: str, to_trash: bool = True) -> tuple[bool, str]:
    try:
        if to_trash:
            ok = QFile.moveToTrash(path)
            if isinstance(ok, tuple):
                ok = ok[0]
            if ok:
                return True, ""
            # Respaldo con gio (p. ej. en sistemas de archivos sin papelera Qt)
            r = run_host(["gio", "trash", path], timeout=30)
            if r.returncode == 0:
                return True, ""
            return False, r.stderr.strip() or "No se pudo mover a la papelera."
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.unlink(path)
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)


# ------------------------------------------------------------------ copiar / mover

def copy_or_move(src: str, dest_dir: str, move: bool, overwrite: str = "ask",
                 ask_cb=None) -> tuple[str | None, str]:
    """Copia o mueve `src` a `dest_dir`.

    overwrite: 'ask' | 'replace' | 'skip' | 'rename'. ask_cb(dest) -> uno de esos valores
    (sin 'ask'), o None para cancelar todo.
    Devuelve (ruta destino o None, mensaje de error / 'cancel' / 'skip').
    """
    dest = Path(dest_dir) / Path(src).name
    if Path(src).resolve() == dest.resolve():
        if move:
            return str(dest), ""
        dest = unique_path(dest.with_name(dest.stem + " - copia" + dest.suffix))
    elif dest.exists():
        mode = overwrite
        if mode == "ask":
            mode = ask_cb(str(dest)) if ask_cb else "rename"
            if mode is None:
                return None, "cancel"
        if mode == "skip":
            return None, "skip"
        if mode == "rename":
            dest = unique_path(dest)
        elif mode == "replace":
            if dest.is_dir() and not dest.is_symlink():
                shutil.rmtree(dest)
            else:
                dest.unlink()
    try:
        if move:
            shutil.move(src, str(dest))
            move_description(src, str(dest))
        elif os.path.isdir(src) and not os.path.islink(src):
            shutil.copytree(src, str(dest), symlinks=True)
        else:
            shutil.copy2(src, str(dest))
        return str(dest), ""
    except Exception as e:  # noqa: BLE001
        return None, str(e)


# ------------------------------------------------------------------ portapapeles de archivos

def set_file_clipboard(paths: list[str], cut: bool) -> None:
    md = QMimeData()
    urls = [QUrl.fromLocalFile(p) for p in paths]
    md.setUrls(urls)
    gnome = ("cut" if cut else "copy") + "\n" + "\n".join(u.toString(QUrl.FullyEncoded) for u in urls)
    md.setData("x-special/gnome-copied-files", QByteArray(gnome.encode()))
    md.setData("x-special/mate-copied-files", QByteArray(gnome.encode()))
    md.setText("\n".join(paths))
    QGuiApplication.clipboard().setMimeData(md)


def get_file_clipboard() -> tuple[list[str], bool]:
    md = QGuiApplication.clipboard().mimeData()
    if md is None:
        return [], False
    cut = False
    paths: list[str] = []
    for fmt in ("x-special/gnome-copied-files", "x-special/mate-copied-files"):
        if md.hasFormat(fmt):
            lines = bytes(md.data(fmt)).decode(errors="replace").splitlines()
            if lines:
                cut = lines[0].strip() == "cut"
                for ln in lines[1:]:
                    u = urlparse(ln.strip())
                    if u.scheme == "file":
                        paths.append(unquote(u.path))
            if paths:
                return paths, cut
    if md.hasUrls():
        paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
    return paths, cut


# ------------------------------------------------------------------ descript.ion

def _desc_file(folder: str) -> Path:
    return Path(folder) / "descript.ion"


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.rstrip("\r\n")
    if not line:
        return None
    if line.startswith('"'):
        end = line.find('"', 1)
        if end < 0:
            return None
        return line[1:end], line[end + 1:].strip()
    name, _, desc = line.partition(" ")
    return name, desc.strip()


def read_descriptions(folder: str) -> dict[str, str]:
    f = _desc_file(folder)
    out: dict[str, str] = {}
    if not f.is_file():
        return out
    try:
        raw = f.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        for line in text.splitlines():
            r = _parse_line(line)
            if r:
                out[r[0].lower()] = r[1]
    except OSError:
        pass
    return out


def write_description(path: str, desc: str) -> None:
    folder, name = os.path.split(path)
    f = _desc_file(folder)
    lines: list[str] = []
    if f.is_file():
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    kept = [ln for ln in lines if (_parse_line(ln) or ("", ""))[0].lower() != name.lower()]
    if desc.strip():
        quoted = f'"{name}"' if " " in name else name
        kept.append(f"{quoted} {desc.strip()}")
    if kept:
        f.write_text("\n".join(kept) + "\n", encoding="utf-8")
    elif f.exists():
        f.unlink()


def move_description(src: str, dest: str) -> None:
    try:
        d = read_descriptions(os.path.dirname(src)).get(os.path.basename(src).lower())
        if d:
            write_description(src, "")
            write_description(dest, d)
    except OSError:
        pass


# ------------------------------------------------------------------ fondo de escritorio

def _desktop() -> str:
    d = (os.environ.get("XDG_CURRENT_DESKTOP", "") + ":" + os.environ.get("DESKTOP_SESSION", "")).lower()
    for name in ("cinnamon", "mate", "xfce", "gnome", "kde", "lxqt", "budgie"):
        if name in d:
            return name
    return "cinnamon"


_WP_SCHEMAS = {
    "cinnamon": ("org.cinnamon.desktop.background", "picture-uri", "picture-options"),
    "gnome": ("org.gnome.desktop.background", "picture-uri", "picture-options"),
    "budgie": ("org.gnome.desktop.background", "picture-uri", "picture-options"),
    "mate": ("org.mate.background", "picture-filename", "picture-options"),
}


def set_wallpaper(image, mode: str) -> tuple[bool, str]:
    """image: ruta o PIL.Image. mode: centered | tiled | stretched | zoom | scaled."""
    from PIL import Image

    dest = data_dir() / "fondo_retrovisor.png"
    try:
        im = Image.open(image) if isinstance(image, str) else image
        im.save(dest, "PNG")
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    desk = _desktop()
    s = settings()
    if desk in _WP_SCHEMAS:
        schema, key, optkey = _WP_SCHEMAS[desk]
        if not s.value_raw("wallpaper/prev_value"):
            prev = run_host(["gsettings", "get", schema, key], timeout=5).stdout.strip()
            prevopt = run_host(["gsettings", "get", schema, optkey], timeout=5).stdout.strip()
            s.set("wallpaper/prev_value", prev)
            s.set("wallpaper/prev_option", prevopt)
        value = str(dest) if key == "picture-filename" else "file://" + quote(str(dest))
        opt = {"tiled": "wallpaper", "centered": "centered", "stretched": "stretched",
               "zoom": "zoom", "scaled": "scaled"}.get(mode, "zoom")
        r1 = run_host(["gsettings", "set", schema, key, value], timeout=5)
        run_host(["gsettings", "set", schema, optkey, opt], timeout=5)
        if desk == "gnome":
            run_host(["gsettings", "set", schema, "picture-uri-dark", value], timeout=5)
        if r1.returncode != 0:
            return False, r1.stderr.strip()
        return True, ""
    if desk == "xfce":
        r = run_host(["xfconf-query", "-c", "xfce4-desktop", "-l"], timeout=5)
        style = {"centered": "1", "tiled": "2", "stretched": "3", "scaled": "4", "zoom": "5"}.get(mode, "5")
        props = [ln for ln in r.stdout.splitlines() if ln.endswith("/last-image")]
        for prop in props:
            run_host(["xfconf-query", "-c", "xfce4-desktop", "-p", prop, "-s", str(dest)], timeout=5)
            run_host(["xfconf-query", "-c", "xfce4-desktop", "-p", prop.replace("last-image", "image-style"), "-s", style], timeout=5)
        return bool(props), "" if props else "No se encontró la configuración de Xfce."
    if desk == "kde":
        script = ("var a=desktops();for(var i=0;i<a.length;i++){var d=a[i];d.wallpaperPlugin='org.kde.image';"
                  "d.currentConfigGroup=['Wallpaper','org.kde.image','General'];"
                  f"d.writeConfig('Image','file://{dest}');}}")
        r = run_host(["qdbus", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script], timeout=10)
        return r.returncode == 0, r.stderr.strip()
    if spawn_host(["feh", "--bg-" + {"tiled": "tile", "centered": "center", "stretched": "scale"}.get(mode, "fill"), str(dest)]):
        return True, ""
    return False, "Escritorio no compatible."


def restore_wallpaper() -> tuple[bool, str]:
    s = settings()
    prev = s.value_raw("wallpaper/prev_value")
    if not prev:
        return False, "No hay un fondo anterior guardado."
    desk = _desktop()
    if desk not in _WP_SCHEMAS:
        return False, "Escritorio no compatible."
    schema, key, optkey = _WP_SCHEMAS[desk]
    run_host(["gsettings", "set", schema, key, str(prev)], timeout=5)
    if desk == "gnome":
        run_host(["gsettings", "set", schema, "picture-uri-dark", str(prev)], timeout=5)
    prevopt = s.value_raw("wallpaper/prev_option")
    if prevopt:
        run_host(["gsettings", "set", schema, optkey, str(prevopt)], timeout=5)
    s.set("wallpaper/prev_value", "")
    return True, ""


# ------------------------------------------------------------------ programas externos

def open_with_default(path: str) -> bool:
    return spawn_host(["xdg-open", path])


def open_in_file_manager(path: str) -> bool:
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    for fm in (["nemo", path if not os.path.isdir(path) else folder], ["caja", folder],
               ["nautilus", "--select", path], ["thunar", folder], ["dolphin", "--select", path],
               ["xdg-open", folder]):
        if shutil.which(fm[0]) and spawn_host(fm):
            return True
    return False


def open_terminal(folder: str) -> bool:
    for term in (["x-terminal-emulator"], ["gnome-terminal", f"--working-directory={folder}"],
                 ["mate-terminal", f"--working-directory={folder}"],
                 ["xfce4-terminal", f"--working-directory={folder}"], ["konsole", "--workdir", folder],
                 ["xterm"]):
        if shutil.which(term[0]) and spawn_host(term, cwd=folder):
            return True
    return False


def external_editor_cmd() -> list[str] | None:
    custom = settings().get("general/external_editor")
    if custom:
        import shlex

        return shlex.split(custom)
    for prog in ("gimp", "pinta", "krita", "kolourpaint", "mtpaint", "drawing"):
        if shutil.which(prog):
            return [prog]
    return None


def edit_external(path: str) -> bool:
    cmd = external_editor_cmd()
    if cmd is None:
        return open_with_default(path)
    return spawn_host(cmd + [path])


def acquire_scanner() -> bool:
    for prog in (["simple-scan"], ["xsane"], ["skanlite"], ["gscan2pdf"]):
        if shutil.which(prog[0]) and spawn_host(prog):
            return True
    return False


# ------------------------------------------------------------------ integración con el escritorio

IMAGE_MIMES = ["image/jpeg", "image/png", "image/gif", "image/bmp", "image/x-bmp", "image/x-ms-bmp",
               "image/tiff", "image/webp", "image/x-pcx", "image/x-tga", "image/x-portable-pixmap",
               "image/x-portable-graymap", "image/x-portable-bitmap", "image/x-portable-anymap",
               "image/x-icon", "image/vnd.microsoft.icon", "image/vnd.adobe.photoshop", "image/avif",
               "image/heif", "image/heic", "image/jp2", "image/x-xpixmap", "image/x-xbitmap"]


def integrate_desktop() -> tuple[bool, str]:
    """Crea la entrada del menú de aplicaciones apuntando a este AppImage (o al script)."""
    import sys

    from .util import resource_dir

    target = os.environ.get("APPIMAGE")
    if target:
        exe = f'"{target}"'
    elif getattr(sys, "frozen", False):
        exe = f'"{sys.executable}"'
    else:
        exe = f'"{sys.executable}" -m retrovisor'
    home = Path.home()
    apps = home / ".local/share/applications"
    icons = home / ".local/share/icons/hicolor/256x256/apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    src_icon = resource_dir() / "icons" / "retrovisor.png"
    if src_icon.exists():
        shutil.copy2(src_icon, icons / "retrovisor.png")
    desktop = f"""[Desktop Entry]
Type=Application
Name=RetroVisor
GenericName=Visor de imágenes
Comment=Visor y explorador de imágenes rápido con interfaz clásica
Exec={exe} %F
Icon=retrovisor
Terminal=false
Categories=Graphics;Viewer;RasterGraphics;2DGraphics;Photography;
MimeType={';'.join(IMAGE_MIMES)};
StartupWMClass=RetroVisor
Actions=Slideshow;

[Desktop Action Slideshow]
Name=Presentación
Exec={exe} --slideshow %F
"""
    path = apps / "retrovisor.desktop"
    path.write_text(desktop, encoding="utf-8")
    os.chmod(path, 0o755)
    run_host(["update-desktop-database", str(apps)], timeout=20)
    run_host(["gtk-update-icon-cache", "-f", "-t", str(home / ".local/share/icons/hicolor")], timeout=20)
    return True, str(path)


def set_default_viewer() -> tuple[bool, str]:
    ok, msg = integrate_desktop()
    if not ok:
        return ok, msg
    r = run_host(["xdg-mime", "default", "retrovisor.desktop", *IMAGE_MIMES], timeout=20)
    if r.returncode != 0:
        return False, r.stderr.strip() or "xdg-mime falló"
    return True, ""
