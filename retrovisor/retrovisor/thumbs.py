"""Generación de miniaturas en segundo plano con caché en disco."""
from __future__ import annotations

import hashlib
import json
import os
import shutil

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from .imaging import ImageInfo, make_thumbnail
from .util import cache_dir

CACHE_VERSION = 2


def _cache_root():
    p = cache_dir() / "miniaturas"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _key(path: str, size: int, autorot: bool) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    h = hashlib.md5(f"{CACHE_VERSION}|{path}|{st.st_size}|{st.st_mtime_ns}|{size}|{int(autorot)}".encode()).hexdigest()
    return h


def cache_size() -> int:
    total = 0
    for root, _dirs, files in os.walk(_cache_root()):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clear_cache() -> None:
    shutil.rmtree(_cache_root(), ignore_errors=True)


class _Signals(QObject):
    done = Signal(int, str, object, object)  # generación, ruta, QImage|None, ImageInfo


class ThumbJob(QRunnable):
    def __init__(self, gen: int, path: str, size: int, use_cache: bool, autorot: bool,
                 signals: _Signals, state: dict):
        super().__init__()
        self.gen, self.path, self.size = gen, path, size
        self.use_cache, self.autorot, self.signals = use_cache, autorot, signals
        self.state = state  # compartido con el cargador: {"gen": generación actual}
        self.setAutoDelete(True)

    @property
    def cancelled(self) -> bool:
        return self.state["gen"] != self.gen

    def run(self):
        if self.cancelled:
            return
        key = _key(self.path, self.size, self.autorot) if self.use_cache else None
        img: QImage | None = None
        info = ImageInfo()
        if key:
            d = _cache_root() / key[:2]
            png, meta = d / (key + ".png"), d / (key + ".json")
            if png.exists() and meta.exists():
                try:
                    m = json.loads(meta.read_text())
                    info = ImageInfo(**m)
                    img = QImage(str(png))
                    if img.isNull():
                        img = None
                except Exception:
                    img = None
        if img is None:
            img, info = make_thumbnail(self.path, self.size, self.autorot)
            if key and img is not None:
                try:
                    d = _cache_root() / key[:2]
                    d.mkdir(exist_ok=True)
                    img.save(str(d / (key + ".png")), "PNG", 50)
                    (d / (key + ".json")).write_text(json.dumps(info.__dict__))
                except Exception:
                    pass
        if not self.cancelled:
            try:
                self.signals.done.emit(self.gen, self.path, img, info)
            except RuntimeError:
                pass


class ThumbLoader(QObject):
    """Cola de miniaturas. Cada cambio de carpeta incrementa la generación y cancela lo pendiente."""

    thumbnail_ready = Signal(str, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(2, min(6, (os.cpu_count() or 2))))
        self.gen = 0
        self._signals = _Signals()
        self._signals.done.connect(self._on_done)
        self._state = {"gen": 0}

    def reset(self):
        self.gen += 1
        self._state["gen"] = self.gen
        self.pool.clear()

    def request(self, paths: list[str], size: int, use_cache: bool, autorot: bool):
        for p in paths:
            self.pool.start(ThumbJob(self.gen, p, size, use_cache, autorot, self._signals, self._state))

    def _on_done(self, gen, path, img, info):
        if gen != self.gen:
            return
        self.thumbnail_ready.emit(path, img, info)

    def shutdown(self):
        self.reset()
        self.pool.waitForDone(2000)
