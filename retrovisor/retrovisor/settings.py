"""Opciones persistentes del programa (~/.config/retrovisor/retrovisor.ini)."""
from __future__ import annotations

from PySide6.QtCore import QSettings

from .util import config_dir

DEFAULTS = {
    # Apariencia
    "appearance/theme": "light",          # light | dark | system
    "appearance/style": "Windows",        # Windows (clásico) | Fusion
    # General
    "general/confirm_delete": True,
    "general/use_trash": True,
    "general/exif_autorotate": False,
    "general/startup_folder": "",          # vacío = última carpeta
    "general/last_folder": "",
    "general/external_editor": "",
    "general/recent_copy_dirs": [],
    "general/favorites": [],
    # Explorador
    "browser/mode": "thumbs",              # thumbs | large | small | list | details
    "browser/thumb_size": 96,
    "browser/sort_key": "name",            # name | ext | size | date | props
    "browser/sort_desc": False,
    "browser/filter": "all",               # all | images | custom
    "browser/filter_custom": "*.jpg;*.png",
    "browser/show_hidden": False,
    "browser/show_folders": True,
    "browser/show_tree": True,
    "browser/show_preview": True,
    "browser/show_toolbar": True,
    "browser/show_statusbar": True,
    "browser/thumb_cache": True,
    "browser/thumb_show_name": True,
    "browser/thumb_show_info": False,
    # Visor
    "viewer/bgcolor": "#000000",
    "viewer/bgcolor_dark": "#000000",
    "viewer/fit": "shrink",                # shrink | fit | none
    "viewer/zoom_lock": False,
    "viewer/smooth": True,
    "viewer/fullscreen": False,
    "viewer/wheel": "navigate",            # navigate | zoom
    "viewer/wrap": True,
    "viewer/show_toolbar": True,
    "viewer/show_statusbar": True,
    "viewer/fs_show_info": False,
    "viewer/preload": True,
    # Presentación
    "slideshow/delay": 3.0,
    "slideshow/loop": True,
    "slideshow/random": False,
    "slideshow/fullscreen": True,
    "slideshow/reverse": False,
    # JPEG / MozJPEG
    "jpeg/quality": 85,
    "jpeg/progressive": False,
    "jpeg/optimize": True,
    "jpeg/subsampling": "4:2:0",
    "jpeg/keep_exif": True,
    "mozjpeg/quality": 80,
    "mozjpeg/progressive": True,
    "mozjpeg/trellis": True,
    "mozjpeg/subsampling": "4:2:0",
    "mozjpeg/tune": "psnr-hvs",            # psnr-hvs | ssim | psnr | ms-ssim
    "mozjpeg/keep_exif": True,
    "png/compress": 6,
    "webp/quality": 85,
    "webp/lossless": False,
    "tiff/compression": "tiff_lzw",
    # Impresión
    "print/mode": "fit",                   # fit | actual | scale
    "print/scale": 100,
    "print/caption": False,
}


class Settings:
    def __init__(self):
        self._q = QSettings(str(config_dir() / "retrovisor.ini"), QSettings.IniFormat)

    def get(self, key, default=None):
        if default is None:
            default = DEFAULTS.get(key)
        val = self._q.value(key, default)
        if isinstance(default, bool):
            if isinstance(val, str):
                return val.lower() in ("1", "true", "yes")
            return bool(val)
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default
        if isinstance(default, float):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        if isinstance(default, list):
            if val is None or val == "":
                return []
            if isinstance(val, str):
                return [val]
            return list(val)
        return val

    def set(self, key, value):
        self._q.setValue(key, value)

    def value_raw(self, key):
        return self._q.value(key)

    def sync(self):
        self._q.sync()


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
