"""Ventana del visor: muestra una imagen, navega por la lista, zoom, presentación y edición básica."""
from __future__ import annotations

import datetime as _dt
import os
import random
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps
from PySide6.QtCore import (QObject, QPoint, QPointF, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, Signal)
from PySide6.QtGui import QAction, QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractScrollArea, QApplication, QDialog, QInputDialog, QLabel, QMainWindow,
                               QMenu, QMessageBox, QToolBar)

from . import dialogs, fileops, imaging
from .imaging import LoadedImage, pil_to_qimage
from .settings import settings
from .theme import icon, is_dark
from .util import human_size

ZOOM_STEPS = [0.0625, 0.083, 0.125, 0.167, 0.25, 0.333, 0.5, 0.667, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0,
              4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 16.0]


# ============================================================ carga asíncrona

class _LoadSignals(QObject):
    loaded = Signal(str, object)  # ruta, LoadedImage | Exception


class _LoadJob(QRunnable):
    def __init__(self, path: str, autorot: bool, sig: _LoadSignals):
        super().__init__()
        self.path, self.autorot, self.sig = path, autorot, sig
        self.setAutoDelete(True)

    def run(self):
        try:
            res = imaging.load_image(self.path, autorotate=self.autorot)
        except Exception as e:  # noqa: BLE001
            res = e
        try:
            self.sig.loaded.emit(self.path, res)
        except RuntimeError:
            pass


# ============================================================ lienzo

class ImageCanvas(QAbstractScrollArea):
    zoomChanged = Signal(float)
    requestNext = Signal()
    requestPrev = Signal()
    doubleClicked = Signal()
    selectionChanged = Signal(QRect)
    contextMenuRequested = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QAbstractScrollArea.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.image: QImage | None = None
        self.pixmap: QPixmap | None = None
        self._scaled: QPixmap | None = None
        self._scaled_zoom = 0.0
        self.frames: list = []
        self.frame_idx = 0
        self.anim = QTimer(self, singleShot=True)
        self.anim.timeout.connect(self._next_frame)
        self.zoom = 1.0
        self.fit_mode = settings().get("viewer/fit")
        self.zoom_locked = settings().get("viewer/zoom_lock")
        self.smooth = settings().get("viewer/smooth")
        self.bg = QColor(settings().get("viewer/bgcolor"))
        self.overlay_text = ""
        self.message = ""
        self._drag_start: QPoint | None = None
        self._drag_scroll = QPoint()
        self._sel_start: QPointF | None = None
        self.selection = QRect()  # en coordenadas de imagen

    # ---------------------------------------------------------------- imagen
    def set_image(self, qimage: QImage | None, frames=None, keep_view: bool = False):
        self.anim.stop()
        self.image = qimage
        self.pixmap = QPixmap.fromImage(qimage) if qimage is not None and not qimage.isNull() else None
        self._scaled = None
        self.frames = frames or []
        self.frame_idx = 0
        self.selection = QRect()
        self.message = ""
        if not keep_view:
            if not self.zoom_locked:
                self.zoom = self._fit_zoom()
        self._update_scrollbars()
        if not keep_view:
            self._center()
        if len(self.frames) > 1:
            self.anim.start(self.frames[0].duration)
        self.viewport().update()
        self.zoomChanged.emit(self.zoom)

    def set_message(self, text: str):
        self.anim.stop()
        self.image = self.pixmap = self._scaled = None
        self.frames = []
        self.message = text
        self._update_scrollbars()
        self.viewport().update()

    def _next_frame(self):
        if len(self.frames) < 2 or not self.isVisible():
            return
        self.frame_idx = (self.frame_idx + 1) % len(self.frames)
        fr = self.frames[self.frame_idx]
        self.pixmap = QPixmap.fromImage(fr.image)
        self._scaled = None
        self.viewport().update()
        self.anim.start(fr.duration)

    def _img_size(self) -> QSize:
        return self.pixmap.size() if self.pixmap else QSize(0, 0)

    def _fit_zoom(self) -> float:
        if not self.pixmap:
            return 1.0
        vw, vh = self.viewport().width(), self.viewport().height()
        iw, ih = self._img_size().width(), self._img_size().height()
        if iw == 0 or ih == 0 or vw < 10 or vh < 10:
            return 1.0
        z = min(vw / iw, vh / ih)
        if self.fit_mode == "shrink":
            return min(1.0, z)
        if self.fit_mode == "fit":
            return z
        return 1.0

    # ---------------------------------------------------------------- zoom
    def set_zoom(self, z: float, anchor: QPointF | None = None):
        if not self.pixmap:
            return
        z = max(0.01, min(32.0, z))
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        img_pt = self.map_to_image(anchor)
        self.zoom = z
        self._scaled = None
        self._update_scrollbars()
        # mantener el punto bajo el cursor
        off = self._offset()
        new_view = QPointF(img_pt.x() * z + off.x(), img_pt.y() * z + off.y())
        self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() + new_view.x() - anchor.x()))
        self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() + new_view.y() - anchor.y()))
        self.viewport().update()
        self.zoomChanged.emit(self.zoom)

    def zoom_in(self, anchor=None):
        nxt = next((s for s in ZOOM_STEPS if s > self.zoom * 1.001), ZOOM_STEPS[-1])
        self.set_zoom(nxt, anchor)

    def zoom_out(self, anchor=None):
        prv = next((s for s in reversed(ZOOM_STEPS) if s < self.zoom * 0.999), ZOOM_STEPS[0])
        self.set_zoom(prv, anchor)

    def zoom_fit(self):
        old = self.fit_mode
        self.fit_mode = "fit" if old == "none" else old
        z = self._fit_zoom()
        self.fit_mode = old
        self.set_zoom(z)
        self._center()

    def zoom_actual(self):
        self.set_zoom(1.0)

    def refit(self):
        if not self.zoom_locked:
            self.zoom = self._fit_zoom()
            self._scaled = None
            self._update_scrollbars()
            self._center()
            self.viewport().update()
            self.zoomChanged.emit(self.zoom)

    # ---------------------------------------------------------------- geometría
    def _scaled_size(self) -> QSize:
        s = self._img_size()
        return QSize(max(1, round(s.width() * self.zoom)), max(1, round(s.height() * self.zoom)))

    def _update_scrollbars(self):
        ss = self._scaled_size() if self.pixmap else QSize(0, 0)
        vw, vh = self.viewport().width(), self.viewport().height()
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setRange(0, max(0, ss.width() - vw))
        vb.setRange(0, max(0, ss.height() - vh))
        hb.setPageStep(vw)
        vb.setPageStep(vh)
        hb.setSingleStep(max(16, vw // 20))
        vb.setSingleStep(max(16, vh // 20))

    def _center(self):
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setValue((hb.maximum()) // 2)
        vb.setValue((vb.maximum()) // 2)

    def _offset(self) -> QPointF:
        ss = self._scaled_size()
        vw, vh = self.viewport().width(), self.viewport().height()
        x = (vw - ss.width()) / 2 if ss.width() < vw else -self.horizontalScrollBar().value()
        y = (vh - ss.height()) / 2 if ss.height() < vh else -self.verticalScrollBar().value()
        return QPointF(x, y)

    def map_to_image(self, p: QPointF) -> QPointF:
        off = self._offset()
        return QPointF((p.x() - off.x()) / self.zoom, (p.y() - off.y()) / self.zoom)

    def map_from_image(self, p: QPointF) -> QPointF:
        off = self._offset()
        return QPointF(p.x() * self.zoom + off.x(), p.y() * self.zoom + off.y())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.zoom_locked and self.pixmap:
            self.zoom = self._fit_zoom()
            self._scaled = None
            self.zoomChanged.emit(self.zoom)
        self._update_scrollbars()

    def scrollContentsBy(self, dx, dy):
        self.viewport().update()

    # ---------------------------------------------------------------- pintura
    def paintEvent(self, e):
        p = QPainter(self.viewport())
        p.fillRect(self.viewport().rect(), self.bg)
        if self.pixmap:
            off = self._offset()
            ss = self._scaled_size()
            target = QRectF(off.x(), off.y(), ss.width(), ss.height())
            if abs(self.zoom - 1.0) < 1e-6:
                p.drawPixmap(off.toPoint(), self.pixmap)
            elif self.zoom < 1.0 and self.smooth and ss.width() * ss.height() < 60_000_000:
                if self._scaled is None or self._scaled_zoom != self.zoom:
                    self._scaled = self.pixmap.scaled(ss, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    self._scaled_zoom = self.zoom
                p.drawPixmap(off.toPoint(), self._scaled)
            else:
                p.setRenderHint(QPainter.SmoothPixmapTransform, self.smooth and self.zoom < 4)
                vis = QRectF(self.viewport().rect()).intersected(target)
                src = QRectF((vis.x() - off.x()) / self.zoom, (vis.y() - off.y()) / self.zoom,
                             vis.width() / self.zoom, vis.height() / self.zoom)
                p.drawPixmap(vis, self.pixmap, src)
            if not self.selection.isNull():
                tl = self.map_from_image(QPointF(self.selection.topLeft()))
                br = self.map_from_image(QPointF(self.selection.x() + self.selection.width(),
                                                 self.selection.y() + self.selection.height()))
                r = QRectF(tl, br)
                p.setPen(QPen(Qt.black, 1, Qt.SolidLine))
                p.drawRect(r)
                p.setPen(QPen(Qt.white, 1, Qt.DashLine))
                p.drawRect(r)
        elif self.message:
            p.setPen(QColor(200, 200, 200) if self.bg.lightness() < 128 else QColor(40, 40, 40))
            p.drawText(self.viewport().rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)
        if self.overlay_text:
            f = p.font()
            f.setPointSize(10)
            p.setFont(f)
            r = p.fontMetrics().boundingRect(QRect(0, 0, 2000, 200), 0, self.overlay_text).adjusted(-6, -4, 6, 4)
            r.moveTopLeft(QPoint(10, 10))
            p.fillRect(r, QColor(0, 0, 0, 150))
            p.setPen(Qt.white)
            p.drawText(r.adjusted(6, 4, -6, -4), 0, self.overlay_text)
        p.end()

    # ---------------------------------------------------------------- ratón
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.pixmap:
            if e.modifiers() & Qt.ShiftModifier:
                self._sel_start = self.map_to_image(e.position())
                self.selection = QRect()
            else:
                if not self.selection.isNull():
                    self.selection = QRect()
                    self.selectionChanged.emit(self.selection)
                    self.viewport().update()
                self._drag_start = e.position().toPoint()
                self._drag_scroll = QPoint(self.horizontalScrollBar().value(), self.verticalScrollBar().value())
                self.viewport().setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.MiddleButton:
            self.doubleClicked.emit()
        elif e.button() == Qt.BackButton:
            self.requestPrev.emit()
        elif e.button() == Qt.ForwardButton:
            self.requestNext.emit()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            d = e.position().toPoint() - self._drag_start
            self.horizontalScrollBar().setValue(self._drag_scroll.x() - d.x())
            self.verticalScrollBar().setValue(self._drag_scroll.y() - d.y())
        elif self._sel_start is not None:
            cur = self.map_to_image(e.position())
            r = QRectF(self._sel_start, cur).normalized().toRect()
            self.selection = r.intersected(QRect(QPoint(0, 0), self._img_size()))
            self.viewport().update()
        else:
            big = self.horizontalScrollBar().maximum() > 0 or self.verticalScrollBar().maximum() > 0
            self.viewport().setCursor(Qt.OpenHandCursor if big else Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drag_start is not None:
            self._drag_start = None
            self.viewport().setCursor(Qt.ArrowCursor)
        if self._sel_start is not None:
            self._sel_start = None
            if self.selection.width() < 2 or self.selection.height() < 2:
                self.selection = QRect()
            self.selectionChanged.emit(self.selection)
            self.viewport().update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.doubleClicked.emit()

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        if dy == 0:
            return
        if e.modifiers() & Qt.ControlModifier or settings().get("viewer/wheel") == "zoom":
            if dy > 0:
                self.zoom_in(e.position())
            else:
                self.zoom_out(e.position())
        elif e.modifiers() & Qt.ShiftModifier:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - dy)
        else:
            if dy < 0:
                self.requestNext.emit()
            else:
                self.requestPrev.emit()
        e.accept()

    def contextMenuEvent(self, e):
        self.contextMenuRequested.emit(e.globalPos())

    def keyPressEvent(self, e):
        # La ventana decide qué hacer con las flechas y Re/Av Pág.
        e.ignore()


# ============================================================ ventana

class ViewerWindow(QMainWindow):
    closed = Signal()
    returnToBrowser = Signal(str)       # ruta de la imagen actual
    filesChanged = Signal()

    def __init__(self, paths: list[str], index: int = 0, browser=None, slideshow: bool = False):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.browser = browser
        self.paths = list(paths)
        self.index = max(0, min(index, len(self.paths) - 1)) if self.paths else 0
        self.cur: LoadedImage | None = None
        self.cur_path: str | None = None
        self.modified = False
        self.undo_stack: list[tuple] = []
        self.cache: dict[str, LoadedImage] = {}
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._sig = _LoadSignals()
        self._sig.loaded.connect(self._on_loaded)
        self._pending: set[str] = set()
        self._want: str | None = None
        self.slide_timer = QTimer(self)
        self.slide_timer.timeout.connect(self._slide_step)
        self.slide_order: list[int] = []
        self._was_maximized = False

        self.canvas = ImageCanvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.requestNext.connect(self.next_image)
        self.canvas.requestPrev.connect(self.prev_image)
        self.canvas.doubleClicked.connect(self.toggle_fullscreen)
        self.canvas.zoomChanged.connect(lambda _z: self._update_status())
        self.canvas.contextMenuRequested.connect(self._context_menu)
        self.canvas.selectionChanged.connect(lambda _r: self._update_status())

        self._build_actions()
        self._build_toolbar()
        self._build_status()
        self.on_theme_changed()
        geo = settings().value_raw("viewer/geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1000, 750)
        self.toolbar.setVisible(settings().get("viewer/show_toolbar"))
        self.statusBar().setVisible(settings().get("viewer/show_statusbar"))
        from .theme import app_icon

        self.setWindowIcon(app_icon())
        if slideshow:
            QTimer.singleShot(0, self.start_slideshow)
        elif settings().get("viewer/fullscreen"):
            QTimer.singleShot(0, self.toggle_fullscreen)
        if self.paths:
            self.show_index(self.index)
        else:
            self.canvas.set_message("No hay imágenes")
            self._update_title()

    # ------------------------------------------------------------------ acciones
    def _act(self, text, slot, shortcut=None, icon_name=None, checkable=False, tip=None):
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        a.setCheckable(checkable)
        if tip:
            a.setToolTip(tip)
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_browse = A("&Explorar", self.go_browser, ["Return", "Enter"], "browse",
                          tip="Volver al explorador (Intro)")
        self.a_escape = A("Salir", self._escape, "Escape")
        self.a_prev = A("Imagen &anterior", self.prev_image, ["PgUp", "Backspace"], "prev")
        self.a_next = A("Imagen &siguiente", self.next_image, ["PgDown", "Space"], "next")
        self.a_first = A("&Primera imagen", lambda: self.show_index(0), "Home", "first")
        self.a_last = A("Ú&ltima imagen", lambda: self.show_index(len(self.paths) - 1), "End", "last")
        self.a_zoom_in = A("A&cercar", lambda: self.canvas.zoom_in(), ["+", "=", "Ctrl++"], "zoom-in")
        self.a_zoom_out = A("A&lejar", lambda: self.canvas.zoom_out(), ["-", "Ctrl+-"], "zoom-out")
        self.a_zoom_fit = A("Ajustar a la &ventana", self.canvas.zoom_fit, ["*", "Ctrl+0"], "zoom-fit")
        self.a_zoom_100 = A("&Tamaño real", self.canvas.zoom_actual, ["/", "Ctrl+1"], "zoom-100")
        self.a_zoom_lock = A("&Bloquear zoom", self._toggle_zoom_lock, "Ctrl+L", checkable=True)
        self.a_zoom_lock.setChecked(self.canvas.zoom_locked)
        self.a_fs = A("&Pantalla completa", self.toggle_fullscreen, ["F", "F11", "Alt+Return"], "fullscreen")
        self.a_slide = A("Presentación a&utomática", self.toggle_slideshow, "S",
                         "slideshow", checkable=True)
        self.a_rot_l = A("Rotar a la &izquierda", lambda: self.transform("270"), "[", "rotate-left")
        self.a_rot_r = A("Rotar a la &derecha", lambda: self.transform("90"), "]", "rotate-right")
        self.a_rot_180 = A("Rotar &180°", lambda: self.transform("180"), None, None)
        self.a_flip_h = A("Voltear &horizontalmente", lambda: self.transform("horizontal"), "Ctrl+H", "flip-h")
        self.a_flip_v = A("Voltear &verticalmente", lambda: self.transform("vertical"), "Ctrl+Shift+V", "flip-v")
        self.a_resize = A("&Redimensionar...", self.resize_image, "Ctrl+R", "resize")
        self.a_crop = A("Re&cortar a la selección", self.crop, "Ctrl+X", "crop")
        self.a_adjust = A("&Ajustes de color...", self.adjust, "Ctrl+J", "adjust")
        self.a_gray = A("Escala de &grises", lambda: self.filter_op("gray"))
        self.a_neg = A("&Negativo", lambda: self.filter_op("negative"))
        self.a_sharpen = A("&Enfocar", lambda: self.filter_op("sharpen"))
        self.a_blur = A("&Desenfocar", lambda: self.filter_op("blur"))
        self.a_autocontrast = A("Auto&contraste", lambda: self.filter_op("autocontrast"))
        self.a_undo = A("&Deshacer", self.undo, "Ctrl+Z", "undo")
        self.a_copy = A("&Copiar imagen", self.copy_image, "Ctrl+C", "copy")
        self.a_paste = A("&Pegar imagen", self.paste_image, "Ctrl+V", "paste")
        self.a_save = A("&Guardar", self.save, None, "save")
        self.a_save_as = A("Guardar &como...", self.save_as, "Ctrl+S", "save-as")
        self.a_save_moz = A("Guardar como JPEG &MozJPEG...", lambda: self.save_as("MOZJPEG"), "Ctrl+Shift+S",
                            "save-as")
        self.a_print = A("&Imprimir...", self.print_image, "Ctrl+P", "print")
        self.a_delete = A("&Eliminar", self.delete_current, "Delete", "delete")
        self.a_rename = A("Re&nombrar...", self.rename_current, "F2", "rename")
        self.a_props = A("P&ropiedades...", self.properties, ["Alt+Enter", "Alt+Return", "Ctrl+I"], "properties")
        self.a_edit = A("&Editar con programa externo", self.edit_external, "Ctrl+E", "edit")
        self.a_open_with = A("Abrir con la aplicación &predeterminada", self.open_default)
        self.a_wp_center = A("&Centrado", lambda: self.wallpaper("centered"))
        self.a_wp_tile = A("&Mosaico", lambda: self.wallpaper("tiled"), "Ctrl+W")
        self.a_wp_zoom = A("&Ajustado (llenar)", lambda: self.wallpaper("zoom"))
        self.a_wp_stretch = A("&Estirado", lambda: self.wallpaper("stretched"))
        self.a_wp_restore = A("&Restaurar fondo anterior", self._restore_wallpaper)
        self.a_toolbar = A("Barra de &herramientas", self._toggle_toolbar, "Ctrl+T", checkable=True)
        self.a_statusbar = A("Barra de &estado", self._toggle_statusbar, "Ctrl+B", checkable=True)
        self.a_dark = A("Modo &oscuro", self._toggle_dark, "Ctrl+Shift+D", "dark", checkable=True)
        self.a_options = A("&Opciones...", self.options, "Ctrl+O", "options")
        self.a_close = A("&Cerrar", self.close, "Ctrl+Q", "exit")
        self.a_info = A("Mostrar &información", self._toggle_info, "I", checkable=True)
        self.a_info.setChecked(settings().get("viewer/fs_show_info"))
        self.a_pan_keys = []

    def _build_toolbar(self):
        tb = QToolBar("Barra de herramientas del visor", self)
        tb.setObjectName("viewer_toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for a in (self.a_browse, None, self.a_prev, self.a_next, None, self.a_slide, None,
                  self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_zoom_100, self.a_fs, None,
                  self.a_rot_l, self.a_rot_r, None, self.a_copy, self.a_save_as, self.a_print, None,
                  self.a_delete, self.a_props, self.a_edit):
            if a is None:
                tb.addSeparator()
            else:
                tb.addAction(a)
        self.toolbar = tb
        self.addToolBar(Qt.TopToolBarArea, tb)

    def _build_status(self):
        sb = self.statusBar()
        self.st_index = QLabel()
        self.st_name = QLabel()
        self.st_dims = QLabel()
        self.st_size = QLabel()
        self.st_zoom = QLabel()
        self.st_date = QLabel()
        self.st_mod = QLabel()
        for w, stretch in ((self.st_index, 0), (self.st_name, 1), (self.st_dims, 0), (self.st_size, 0),
                           (self.st_zoom, 0), (self.st_date, 0), (self.st_mod, 0)):
            w.setFrameShape(QLabel.Panel)
            w.setFrameShadow(QLabel.Sunken)
            w.setContentsMargins(4, 0, 4, 0)
            sb.addPermanentWidget(w, stretch) if w is not self.st_name else sb.addWidget(w, 1)

    def on_theme_changed(self):
        dark = is_dark()
        self.a_dark.setChecked(dark) if hasattr(self, "a_dark") else None
        bg = settings().get("viewer/bgcolor")
        self.canvas.bg = QColor(bg)
        self.canvas.viewport().update()
        for name, a in (("browse", self.a_browse), ("prev", self.a_prev), ("next", self.a_next),
                        ("slideshow", self.a_slide), ("zoom-in", self.a_zoom_in), ("zoom-out", self.a_zoom_out),
                        ("zoom-fit", self.a_zoom_fit), ("zoom-100", self.a_zoom_100), ("fullscreen", self.a_fs),
                        ("rotate-left", self.a_rot_l), ("rotate-right", self.a_rot_r), ("copy", self.a_copy),
                        ("save-as", self.a_save_as), ("print", self.a_print), ("delete", self.a_delete),
                        ("properties", self.a_props), ("edit", self.a_edit), ("dark", self.a_dark)):
            a.setIcon(icon(name))

    # ------------------------------------------------------------------ menú contextual
    def _context_menu(self, pos):
        m = QMenu(self)
        m.addAction(self.a_browse)
        m.addSeparator()
        m.addAction(self.a_prev)
        m.addAction(self.a_next)
        m.addAction(self.a_first)
        m.addAction(self.a_last)
        m.addAction(self.a_slide)
        m.addSeparator()
        z = m.addMenu(icon("zoom-in"), "&Zoom")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_zoom_100):
            z.addAction(a)
        z.addSeparator()
        z.addAction(self.a_zoom_lock)
        z.addSeparator()
        for pct in (25, 50, 100, 200, 400):
            z.addAction(f"{pct} %", lambda p=pct: self.canvas.set_zoom(p / 100))
        m.addAction(self.a_fs)
        m.addSeparator()
        ed = m.addMenu(icon("edit"), "&Modificar")
        for a in (self.a_undo, None, self.a_rot_l, self.a_rot_r, self.a_rot_180, self.a_flip_h, self.a_flip_v,
                  None, self.a_resize, self.a_crop, None, self.a_adjust, self.a_autocontrast, self.a_gray,
                  self.a_neg, self.a_sharpen, self.a_blur):
            ed.addSeparator() if a is None else ed.addAction(a)
        self.a_crop.setEnabled(not self.canvas.selection.isNull())
        self.a_undo.setEnabled(bool(self.undo_stack))
        m.addAction(self.a_edit)
        m.addAction(self.a_open_with)
        m.addSeparator()
        m.addAction(self.a_copy)
        m.addAction(self.a_paste)
        m.addAction(self.a_save)
        self.a_save.setEnabled(self.modified)
        m.addAction(self.a_save_as)
        m.addAction(self.a_save_moz)
        m.addAction(self.a_print)
        wp = m.addMenu(icon("wallpaper"), "&Fondo de escritorio")
        for a in (self.a_wp_center, self.a_wp_tile, self.a_wp_zoom, self.a_wp_stretch, None, self.a_wp_restore):
            wp.addSeparator() if a is None else wp.addAction(a)
        m.addSeparator()
        m.addAction(self.a_rename)
        m.addAction(self.a_delete)
        m.addAction(self.a_props)
        m.addSeparator()
        v = m.addMenu("&Ver")
        self.a_toolbar.setChecked(self.toolbar.isVisible())
        self.a_statusbar.setChecked(self.statusBar().isVisible())
        v.addAction(self.a_toolbar)
        v.addAction(self.a_statusbar)
        v.addAction(self.a_info)
        v.addAction(self.a_dark)
        m.addAction(self.a_options)
        m.addSeparator()
        m.addAction(self.a_close)
        m.exec(pos)

    # ------------------------------------------------------------------ navegación
    def _autorot(self) -> bool:
        return settings().get("general/exif_autorotate")

    def _request(self, path: str):
        if path in self.cache or path in self._pending:
            return
        self._pending.add(path)
        self.pool.start(_LoadJob(path, self._autorot(), self._sig))

    def show_index(self, idx: int):
        if not self.paths:
            return
        if not self._maybe_save():
            return
        idx = max(0, min(idx, len(self.paths) - 1))
        self.index = idx
        path = self.paths[idx]
        self._want = path
        self.modified = False
        self.undo_stack.clear()
        if path in self.cache:
            self._display(self.cache[path])
        else:
            self.cur_path = path
            self._update_title()
            self._update_status(loading=True)
            self._request(path)
        self._preload()

    def _preload(self):
        if not self.paths:
            return
        keep = {self.paths[self.index]}
        if settings().get("viewer/preload"):
            for d in (1, -1):
                j = self.index + d
                if 0 <= j < len(self.paths):
                    keep.add(self.paths[j])
                    if self.paths[self.index] in self.cache:
                        self._request(self.paths[j])
        for k in list(self.cache):
            if k not in keep:
                del self.cache[k]

    def _on_loaded(self, path, res):
        self._pending.discard(path)
        if isinstance(res, Exception):
            if path == self._want:
                self.cur = None
                self.cur_path = path
                self.canvas.set_message(f"No se puede mostrar la imagen:\n{os.path.basename(path)}\n\n{res}")
                self._update_title()
                self._update_status()
            return
        self.cache[path] = res
        if path == self._want:
            self._display(res)
            self._preload()

    def _display(self, li: LoadedImage):
        self.cur = li
        self.cur_path = li.path
        self.canvas.set_image(li.qimage, li.frames)
        self._update_title()
        self._update_status()

    def next_image(self):
        if self.slide_timer.isActive():
            self._slide_step()
            return
        if self.index + 1 < len(self.paths):
            self.show_index(self.index + 1)
        elif settings().get("viewer/wrap") and self.paths:
            self.show_index(0)

    def prev_image(self):
        if self.index > 0:
            self.show_index(self.index - 1)
        elif settings().get("viewer/wrap") and self.paths:
            self.show_index(len(self.paths) - 1)

    def _escape(self):
        if self.slide_timer.isActive():
            self.stop_slideshow()
        if self.isFullScreen():
            self.toggle_fullscreen()
        else:
            self.go_browser()

    def keyPressEvent(self, e):
        k = e.key()
        step = 64
        hb, vb = self.canvas.horizontalScrollBar(), self.canvas.verticalScrollBar()
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            horiz = k in (Qt.Key_Left, Qt.Key_Right)
            bar = hb if horiz else vb
            if bar.maximum() == 0 and not e.modifiers() & Qt.ControlModifier:
                # La imagen cabe: las flechas cambian de imagen.
                if k in (Qt.Key_Left, Qt.Key_Up):
                    self.prev_image()
                else:
                    self.next_image()
                return
            if k == Qt.Key_Left:
                hb.setValue(hb.value() - step)
            elif k == Qt.Key_Right:
                hb.setValue(hb.value() + step)
            elif k == Qt.Key_Up:
                vb.setValue(vb.value() - step)
            else:
                vb.setValue(vb.value() + step)
            return
        if self.slide_timer.isActive() and k not in (Qt.Key_S, Qt.Key_Space, Qt.Key_Right, Qt.Key_Left):
            self.stop_slideshow()
        super().keyPressEvent(e)

    # ------------------------------------------------------------------ estado
    def _update_title(self):
        name = os.path.basename(self.cur_path) if self.cur_path else ""
        star = "*" if self.modified else ""
        self.setWindowTitle(f"{name}{star} - RetroVisor" if name else "RetroVisor")

    def _update_status(self, loading: bool = False):
        n = len(self.paths)
        self.st_index.setText(f"{self.index + 1}/{n}" if n else "0/0")
        path = self.cur_path
        self.st_name.setText(os.path.basename(path) if path else "")
        li = self.cur if (self.cur and self.cur.path == path) else None
        if loading:
            self.st_dims.setText("Cargando...")
        elif li:
            extra = f" ({len(li.frames)} cuadros)" if li.animated else ""
            w, h = (li.pil.width, li.pil.height) if li.pil is not None else (li.width, li.height)
            self.st_dims.setText(f"{w}x{h}x{li.bpp}b{extra}")
        else:
            self.st_dims.setText("")
        try:
            st = os.stat(path) if path and os.path.exists(path) else None
        except OSError:
            st = None
        self.st_size.setText(human_size(st.st_size) if st else "")
        self.st_date.setText(_dt.datetime.fromtimestamp(st.st_mtime).strftime("%d/%m/%Y %H:%M") if st else "")
        sel = self.canvas.selection
        zoom_txt = f"{self.canvas.zoom * 100:.0f}%"
        if not sel.isNull():
            zoom_txt += f"  Sel: {sel.width()}x{sel.height()}"
        self.st_zoom.setText(zoom_txt + (" 🔒" if self.canvas.zoom_locked else ""))
        self.st_mod.setText("Modificada" if self.modified else "")
        self.st_mod.setVisible(self.modified)
        if self.isFullScreen() and self.a_info.isChecked() and path:
            self.canvas.overlay_text = (f"{os.path.basename(path)}\n{self.st_dims.text()}  {self.st_size.text()}\n"
                                        f"{self.st_index.text()}  {zoom_txt}")
        else:
            self.canvas.overlay_text = ""
        self.canvas.viewport().update()

    # ------------------------------------------------------------------ vista
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._was_maximized:
                self.showMaximized()
            self.toolbar.setVisible(settings().get("viewer/show_toolbar"))
            self.statusBar().setVisible(settings().get("viewer/show_statusbar"))
            self.canvas.bg = QColor(settings().get("viewer/bgcolor"))
            self.canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.unsetCursor()
        else:
            self._was_maximized = self.isMaximized()
            self.toolbar.hide()
            self.statusBar().hide()
            self.canvas.bg = QColor("#000000")
            self.canvas.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.canvas.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.showFullScreen()
        QTimer.singleShot(50, lambda: (self.canvas.refit(), self._update_status()))

    def _toggle_zoom_lock(self):
        self.canvas.zoom_locked = self.a_zoom_lock.isChecked()
        settings().set("viewer/zoom_lock", self.canvas.zoom_locked)
        self._update_status()

    def _toggle_toolbar(self):
        v = self.a_toolbar.isChecked()
        self.toolbar.setVisible(v)
        settings().set("viewer/show_toolbar", v)

    def _toggle_statusbar(self):
        v = self.a_statusbar.isChecked()
        self.statusBar().setVisible(v)
        settings().set("viewer/show_statusbar", v)

    def _toggle_info(self):
        settings().set("viewer/fs_show_info", self.a_info.isChecked())
        self._update_status()

    def _toggle_dark(self):
        from .theme import apply_theme

        settings().set("appearance/theme", "dark" if self.a_dark.isChecked() else "light")
        apply_theme()

    def options(self):
        from .theme import apply_theme

        if dialogs.OptionsDialog(self).exec() == QDialog.Accepted:
            apply_theme()
            self.canvas.fit_mode = settings().get("viewer/fit")
            self.canvas.smooth = settings().get("viewer/smooth")
            self.canvas.bg = QColor(settings().get("viewer/bgcolor"))
            self.canvas.refit()
            if self.browser:
                self.browser.reload_settings()

    # ------------------------------------------------------------------ presentación
    def start_slideshow(self):
        s = settings()
        n = len(self.paths)
        if n == 0:
            return
        order = list(range(n))
        if s.get("slideshow/random"):
            random.shuffle(order)
        elif s.get("slideshow/reverse"):
            order.reverse()
        if self.index in order:
            pos = order.index(self.index)
            order = order[pos:] + order[:pos]
        self.slide_order = order[1:]
        self.slide_timer.start(int(s.get("slideshow/delay") * 1000))
        self.a_slide.setChecked(True)
        self.a_slide.setIcon(icon("pause"))
        if s.get("slideshow/fullscreen") and not self.isFullScreen():
            self.toggle_fullscreen()
        self.setCursor(Qt.BlankCursor) if self.isFullScreen() else None

    def stop_slideshow(self):
        self.slide_timer.stop()
        self.a_slide.setChecked(False)
        self.a_slide.setIcon(icon("slideshow"))
        self.unsetCursor()

    def toggle_slideshow(self):
        if self.slide_timer.isActive():
            self.stop_slideshow()
        else:
            self.start_slideshow()

    def _slide_step(self):
        if self.modified:
            self.stop_slideshow()
            return
        if not self.slide_order:
            if settings().get("slideshow/loop"):
                n = len(self.paths)
                order = list(range(n))
                if settings().get("slideshow/random"):
                    random.shuffle(order)
                elif settings().get("slideshow/reverse"):
                    order.reverse()
                self.slide_order = order
            else:
                self.stop_slideshow()
                return
        nxt = self.slide_order.pop(0)
        self.show_index(nxt)
        self.slide_timer.start(int(settings().get("slideshow/delay") * 1000))

    # ------------------------------------------------------------------ edición
    def _editable(self) -> bool:
        if self.cur is None or self.cur.pil is None:
            return False
        return True

    def _push_undo(self):
        self.undo_stack.append((self.cur.pil, self.cur.qimage, self.cur.frames, self.modified))
        del self.undo_stack[:-10]

    def _apply(self, newpil: Image.Image):
        self._push_undo()
        self.cur.pil = newpil
        self.cur.qimage = pil_to_qimage(newpil)
        self.cur.frames = []
        self.modified = True
        self.cache.pop(self.cur.path, None)
        self.canvas.set_image(self.cur.qimage)
        self._update_title()
        self._update_status()

    def undo(self):
        if not self.undo_stack or self.cur is None:
            return
        pil, qi, frames, mod = self.undo_stack.pop()
        self.cur.pil, self.cur.qimage, self.cur.frames = pil, qi, frames
        self.modified = mod
        self.canvas.set_image(qi, frames)
        self._update_title()
        self._update_status()

    def transform(self, op: str):
        if not self._editable():
            return
        tr = {"90": Image.Transpose.ROTATE_270, "270": Image.Transpose.ROTATE_90,
              "180": Image.Transpose.ROTATE_180, "horizontal": Image.Transpose.FLIP_LEFT_RIGHT,
              "vertical": Image.Transpose.FLIP_TOP_BOTTOM}[op]
        self._apply(self.cur.pil.transpose(tr))

    def resize_image(self):
        if not self._editable():
            return
        d = dialogs.ResizeDialog(self.cur.pil.width, self.cur.pil.height, self)
        if d.exec() == QDialog.Accepted:
            w, h = d.target_size(self.cur.pil.width, self.cur.pil.height)
            self._apply(self.cur.pil.resize((w, h), d.resample()))

    def crop(self):
        sel = self.canvas.selection
        if not self._editable() or sel.isNull():
            return
        self._apply(self.cur.pil.crop((sel.x(), sel.y(), sel.x() + sel.width(), sel.y() + sel.height())))

    def adjust(self):
        if not self._editable():
            return
        d = dialogs.AdjustDialog(self.cur.pil, self)
        if d.exec() == QDialog.Accepted:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self._apply(d.apply(self.cur.pil))
            finally:
                QApplication.restoreOverrideCursor()

    def filter_op(self, op: str):
        if not self._editable():
            return
        im = self.cur.pil
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        base = im.convert("RGB") if im.mode == "RGBA" else im
        if op == "gray":
            out = base.convert("L")
            alpha = alpha
        elif op == "negative":
            out = ImageOps.invert(base)
        elif op == "sharpen":
            out = base.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
        elif op == "blur":
            out = base.filter(ImageFilter.GaussianBlur(1.5))
        elif op == "autocontrast":
            out = ImageOps.autocontrast(base, cutoff=0.5)
        else:
            return
        if alpha is not None:
            out = out.convert("RGBA")
            out.putalpha(alpha)
        self._apply(out)

    # ------------------------------------------------------------------ guardar
    def _maybe_save(self) -> bool:
        if not self.modified or self.cur is None:
            return True
        r = dialogs.ask_yes_no(self, "RetroVisor", f"La imagen «{os.path.basename(self.cur.path)}» ha sido "
                                                   "modificada.\n¿Desea guardar los cambios?", cancel=True)
        if r is None:
            return False
        if r:
            return self.save()
        self.modified = False
        return True

    def save(self) -> bool:
        if self.cur is None or self.cur.pil is None:
            return False
        ext = Path(self.cur.path).suffix.lower()
        fmt = imaging.EXT_TO_SAVE.get(ext)
        if fmt is None:
            return self.save_as()
        try:
            imaging.save_image(self.cur.pil, self.cur.path, fmt, {}, exif=self.cur.exif, icc=self.cur.icc)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Guardar", f"No se pudo guardar la imagen:\n{e}")
            return False
        self.modified = False
        self.cache.pop(self.cur.path, None)
        self._update_title()
        self._update_status()
        self.filesChanged.emit()
        return True

    def save_as(self, fmt: str | None = None) -> bool:
        if self.cur is None or self.cur.pil is None:
            return False
        if fmt == "MOZJPEG":
            start = str(Path(self.cur.path).with_suffix(".jpg"))
        else:
            start = self.cur.path
        res = dialogs.ask_save_path(self, start, fmt or None)
        if not res:
            return False
        path, key, opts = res
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            imaging.save_image(self.cur.pil, path, key, opts, exif=self.cur.exif, icc=self.cur.icc)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Guardar como", f"No se pudo guardar la imagen:\n{e}")
            return False
        QApplication.restoreOverrideCursor()
        size = os.path.getsize(path)
        self.statusBar().showMessage(f"Guardado: {os.path.basename(path)} ({human_size(size)})", 6000)
        self.modified = False
        if os.path.dirname(path) == os.path.dirname(self.cur.path) and path not in self.paths:
            self.paths.insert(self.index + 1, path)
        self._update_title()
        self._update_status()
        self.filesChanged.emit()
        return True

    # ------------------------------------------------------------------ otras acciones
    def copy_image(self):
        if self.cur is None:
            return
        sel = self.canvas.selection
        qi = self.cur.qimage
        if not sel.isNull():
            qi = qi.copy(sel)
        QGuiApplication.clipboard().setImage(qi)
        self.statusBar().showMessage("Imagen copiada al portapapeles" + (" (selección)" if not sel.isNull() else ""), 3000)

    def paste_image(self):
        qi = QGuiApplication.clipboard().image()
        if qi.isNull():
            QMessageBox.information(self, "Pegar", "El portapapeles no contiene ninguna imagen.")
            return
        if not self._maybe_save():
            return
        pil = imaging.qimage_to_pil(qi)
        folder = os.path.dirname(self.cur_path) if self.cur_path else str(Path.home())
        from .util import unique_path

        new = str(unique_path(Path(folder) / "Pegado.png"))
        self.cur = LoadedImage(path=new, pil=pil, qimage=qi, width=qi.width(), height=qi.height(), bpp=32,
                               format="PNG", mode=pil.mode)
        self.cur_path = new
        self.modified = True
        self.canvas.set_image(qi)
        self._update_title()
        self._update_status()
        self.save_as()

    def print_image(self):
        if self.cur is None:
            return
        dpi = float(self.cur.dpi[0]) if self.cur.dpi else 96.0
        dialogs.print_images(self, [(self.cur.qimage, os.path.basename(self.cur.path), dpi)])

    def delete_current(self):
        if not self.cur_path or not os.path.exists(self.cur_path):
            return
        s = settings()
        trash = s.get("general/use_trash")
        if s.get("general/confirm_delete"):
            q = (f"¿Desea enviar «{os.path.basename(self.cur_path)}» a la papelera?" if trash else
                 f"¿Desea eliminar definitivamente «{os.path.basename(self.cur_path)}»?")
            if not dialogs.ask_yes_no(self, "Confirmar eliminación de archivo", q):
                return
        ok, err = fileops.delete_path(self.cur_path, trash)
        if not ok:
            QMessageBox.critical(self, "Eliminar", f"No se pudo eliminar el archivo:\n{err}")
            return
        self.modified = False
        self.cache.pop(self.cur_path, None)
        del self.paths[self.index]
        self.filesChanged.emit()
        if not self.paths:
            self.cur = None
            self.cur_path = None
            self.canvas.set_message("No hay más imágenes")
            self._update_title()
            self._update_status()
            return
        self.show_index(min(self.index, len(self.paths) - 1))

    def rename_current(self):
        if not self.cur_path:
            return
        old = self.cur_path
        name, ok = QInputDialog.getText(self, "Renombrar", "Nuevo nombre:", text=os.path.basename(old))
        if not ok or not name or name == os.path.basename(old):
            return
        new = os.path.join(os.path.dirname(old), name)
        if os.path.exists(new):
            QMessageBox.warning(self, "Renombrar", "Ya existe un archivo con ese nombre.")
            return
        try:
            os.rename(old, new)
            fileops.move_description(old, new)
        except OSError as e:
            QMessageBox.critical(self, "Renombrar", str(e))
            return
        self.paths[self.index] = new
        if self.cur and self.cur.path == old:
            self.cur.path = new
        self.cache.pop(old, None)
        self.cur_path = new
        self._want = new
        self._update_title()
        self._update_status()
        self.filesChanged.emit()

    def properties(self):
        if self.cur_path and os.path.exists(self.cur_path):
            dialogs.PropertiesDialog(self.cur_path, self.cur, self).exec()

    def edit_external(self):
        if self.cur_path and not fileops.edit_external(self.cur_path):
            QMessageBox.warning(self, "Editar", "No se encontró ningún editor de imágenes.\n"
                                               "Puede configurarlo en Opciones > General.")

    def open_default(self):
        if self.cur_path:
            fileops.open_with_default(self.cur_path)

    def wallpaper(self, mode: str):
        if self.cur is None:
            return
        ok, err = fileops.set_wallpaper(self.cur.pil if self.modified else self.cur.path, mode)
        if ok:
            self.statusBar().showMessage("Fondo de escritorio cambiado", 3000)
        else:
            QMessageBox.warning(self, "Fondo de escritorio", f"No se pudo cambiar el fondo:\n{err}")

    def _restore_wallpaper(self):
        ok, err = fileops.restore_wallpaper()
        if not ok:
            QMessageBox.information(self, "Fondo de escritorio", err)

    def go_browser(self):
        if self.isFullScreen() and self.slide_timer.isActive():
            self.stop_slideshow()
        self.close()

    def closeEvent(self, e):
        if not self._maybe_save():
            e.ignore()
            return
        self.stop_slideshow()
        if not self.isFullScreen():
            settings().set("viewer/geometry", self.saveGeometry())
        self.pool.clear()
        self.returnToBrowser.emit(self.cur_path or "")
        self.closed.emit()
        super().closeEvent(e)
