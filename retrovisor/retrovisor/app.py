"""Punto de entrada: coordina el explorador y el visor como en el programa clásico.

- Sin argumentos o con una carpeta: se abre el explorador.
- Con un archivo de imagen: se abre directamente el visor; Intro vuelve al explorador.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
from urllib.parse import unquote

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import APP_NAME, ORG_NAME, __version__, imaging


class Controller:
    def __init__(self, app: QApplication):
        self.app = app
        self.browser = None
        self.viewers: list = []

    def ensure_browser(self, folder: str | None = None):
        from .browser import BrowserWindow

        if self.browser is None:
            self.browser = BrowserWindow(folder)
            self.browser.openViewer.connect(self.open_viewer)
            self.browser.destroyed.connect(lambda *_: setattr(self, "browser", None))
        return self.browser

    def open_viewer(self, paths: list, index: int, slideshow: bool = False, from_browser: bool = True):
        from .viewer import ViewerWindow

        v = ViewerWindow(paths, index, browser=self.browser, slideshow=slideshow)
        self.viewers.append(v)
        v.returnToBrowser.connect(lambda path, vv=v: self._viewer_closed(vv, path))
        v.filesChanged.connect(lambda: self.browser and self.browser.refresh(keep_selection=True))
        if self.browser is not None and self.browser.isVisible():
            # el visor sustituye a la ventana del explorador
            self.browser.hide()
        v.show()
        v.activateWindow()

    def _viewer_closed(self, viewer, path: str):
        if viewer in self.viewers:
            self.viewers.remove(viewer)
        if self.viewers:
            return
        folder = os.path.dirname(path) if path else None
        b = self.ensure_browser(folder)
        b.show()
        b.raise_()
        b.activateWindow()
        if path:
            QTimer.singleShot(0, lambda: b.select_file(path))


def _install_translations(app: QApplication) -> None:
    """Traduce al español los textos internos de Qt (diálogos de archivo, botones...),
    igual que el resto de la interfaz."""
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    app._translators = []
    for base in ("qtbase", "qt"):
        tr = QTranslator(app)
        if tr.load(QLocale(QLocale.Spanish), base, "_", path):
            app.installTranslator(tr)
            app._translators.append(tr)


def _parse_args(argv):
    p = argparse.ArgumentParser(prog="retrovisor", description="Visor y explorador de imágenes.")
    p.add_argument("paths", nargs="*", help="Imagen(es) o carpeta a abrir")
    p.add_argument("-s", "--slideshow", action="store_true", help="Iniciar una presentación")
    p.add_argument("-f", "--fullscreen", action="store_true", help="Abrir el visor a pantalla completa")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = _parse_args(argv)
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication([sys.argv[0]] + [a for a in argv if a.startswith("-platform") or a.startswith("--platform")])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("retrovisor")
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    _install_translations(app)

    from .settings import settings
    from .theme import app_icon, apply_theme

    apply_theme(app)
    app.setWindowIcon(app_icon())
    ctl = Controller(app)

    paths = [os.path.abspath(os.path.expanduser(unquote(p[7:]) if p.startswith("file://") else p))
             for p in args.paths]
    files = [p for p in paths if os.path.isfile(p)]
    dirs = [p for p in paths if os.path.isdir(p)]

    if files:
        if len(files) == 1:
            # una sola imagen: navegar por todas las imágenes de su carpeta
            folder = os.path.dirname(files[0])
            try:
                names = sorted((n for n in os.listdir(folder) if imaging.is_image(n)
                                and not n.startswith(".")), key=str.lower)
            except OSError:
                names = []
            lst = [os.path.join(folder, n) for n in names] or files
            idx = lst.index(files[0]) if files[0] in lst else 0
            if files[0] not in lst:
                lst.insert(0, files[0])
                idx = 0
        else:
            lst, idx = files, 0
        ctl.open_viewer(lst, idx, slideshow=args.slideshow)
        if args.fullscreen and ctl.viewers and not ctl.viewers[-1].isFullScreen():
            QTimer.singleShot(100, ctl.viewers[-1].toggle_fullscreen)
    else:
        b = ctl.ensure_browser(dirs[0] if dirs else None)
        b.show()
        if args.slideshow:
            QTimer.singleShot(200, b.slideshow)
    app.setQuitOnLastWindowClosed(True)
    rc = app.exec()
    settings().sync()
    return rc


if __name__ == "__main__":
    sys.exit(main())
