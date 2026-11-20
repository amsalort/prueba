"""Ventana del explorador: árbol de carpetas, lista de archivos con miniaturas y vista previa."""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QDir, QFileSystemWatcher, QItemSelection, QItemSelectionModel,
                            QMimeData, QMimeDatabase, QModelIndex, QObject, QRect, QRunnable, QSize, Qt, QThreadPool,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import QAction, QActionGroup, QIcon, QImage, QKeySequence, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
                               QFileSystemModel, QFrame, QInputDialog, QLabel, QListView,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QSizePolicy,
                               QSplitter, QStackedWidget, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTabWidget, QToolBar, QToolButton, QTreeView, QVBoxLayout, QWidget)

from . import dialogs, fileops, imaging
from .imaging import ImageInfo
from .settings import settings
from .theme import app_icon, apply_theme, icon, is_dark
from .thumbs import ThumbLoader
from .util import human_size, kb_size

COLS = ["Nombre", "Tamaño", "Tipo", "Modificado", "Propiedades de imagen", "Descripción"]
_mimedb = QMimeDatabase()
_mime_icons: dict[str, QIcon] = {}


def file_icon(path: str, is_dir: bool) -> QIcon:
    if is_dir:
        return icon("folder")
    mt = _mimedb.mimeTypeForFile(path, QMimeDatabase.MatchExtension)
    key = mt.name()
    if key not in _mime_icons:
        ic = QIcon.fromTheme(mt.iconName())
        if ic.isNull():
            ic = QIcon.fromTheme(mt.genericIconName())
        if ic.isNull():
            ic = QApplication.style().standardIcon(QStyle.SP_FileIcon)
        _mime_icons[key] = ic
    return _mime_icons[key]


def type_name(path: str, is_dir: bool) -> str:
    if is_dir:
        return "Carpeta de archivos"
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".jpe", ".jfif"):
        return "Imagen JPEG"
    mt = _mimedb.mimeTypeForFile(path, QMimeDatabase.MatchExtension)
    c = mt.comment()
    if c and mt.name() != "application/octet-stream":
        return c[0].upper() + c[1:]
    return f"Archivo {ext[1:].upper()}" if ext else "Archivo"


@dataclass
class Entry:
    path: str
    name: str
    is_dir: bool
    size: int
    mtime: float
    is_image: bool
    info: ImageInfo | None = None
    thumb: QPixmap | None = None
    thumb_failed: bool = False
    desc: str = ""

    @property
    def ext(self) -> str:
        return os.path.splitext(self.name)[1].lower()


# ============================================================ modelo

class FolderModel(QAbstractTableModel):
    renameFailed = Signal(str)
    renamed = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list[Entry] = []
        self.folder = ""
        self.index_of: dict[str, int] = {}
        self.thumb_size = settings().get("browser/thumb_size")

    def load(self, folder: str):
        s = settings()
        show_hidden = s.get("browser/show_hidden")
        show_dirs = s.get("browser/show_folders")
        descs = fileops.read_descriptions(folder)
        entries = []
        try:
            with os.scandir(folder) as it:
                for de in it:
                    name = de.name
                    if not show_hidden and name.startswith("."):
                        continue
                    if name.lower() == "descript.ion" and not show_hidden:
                        continue
                    try:
                        is_dir = de.is_dir()
                        st = de.stat()
                    except OSError:
                        continue
                    if is_dir and not show_dirs:
                        continue
                    if not is_dir and not dialogs.match_filter(name):
                        continue
                    entries.append(Entry(de.path, name, is_dir, 0 if is_dir else st.st_size, st.st_mtime,
                                         (not is_dir) and imaging.is_image(name), desc=descs.get(name.lower(), "")))
        except OSError:
            pass
        self.beginResetModel()
        self.folder = folder
        self.entries = entries
        self._sort()
        self.endResetModel()

    def _sort(self):
        s = settings()
        key = s.get("browser/sort_key")
        desc = s.get("browser/sort_desc")
        natural = lambda e: e.name.lower()
        keyf = {
            "name": natural,
            "ext": lambda e: (e.ext, e.name.lower()),
            "size": lambda e: (e.size, e.name.lower()),
            "date": lambda e: (e.mtime, e.name.lower()),
            "props": lambda e: ((e.info.width * e.info.height) if e.info else -1, e.name.lower()),
        }.get(key, natural)
        dirs = sorted([e for e in self.entries if e.is_dir], key=natural)
        files = sorted([e for e in self.entries if not e.is_dir], key=keyf, reverse=desc)
        self.entries = dirs + files
        self.index_of = {e.path: i for i, e in enumerate(self.entries)}

    def resort(self):
        self.layoutAboutToBeChanged.emit()
        old = {e.path: i for i, e in enumerate(self.entries)}
        persistent = self.persistentIndexList()
        self._sort()
        # Reasignar los índices persistentes (selección, actual) siguiendo la ruta de cada archivo.
        new_from = []
        to = []
        inv = {i: p for p, i in old.items()}
        for pi in persistent:
            path = inv.get(pi.row())
            if path is not None and path in self.index_of:
                new_from.append(pi)
                to.append(self.index(self.index_of[path], pi.column()))
        self.changePersistentIndexList(new_from, to)
        self.layoutChanged.emit()

    # ---------------------------------------------------------------- Qt
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole and section == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        c = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == 0:
                return e.name
            if role == Qt.EditRole:
                return None
            if c == 1:
                return "" if e.is_dir else kb_size(e.size)
            if c == 2:
                return type_name(e.path, e.is_dir)
            if c == 3:
                return _dt.datetime.fromtimestamp(e.mtime).strftime("%d/%m/%Y %H:%M")
            if c == 4:
                if e.info and e.info.ok:
                    return f"{e.info.width}x{e.info.height}x{e.info.bpp}b"
                return ""
            if c == 5:
                return e.desc
        if role == Qt.DecorationRole and c == 0:
            return file_icon(e.path, e.is_dir)
        if role == Qt.TextAlignmentRole and c == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            lines = [e.name]
            if not e.is_dir:
                lines.append(f"Tamaño: {human_size(e.size)}")
            if e.info and e.info.ok:
                lines.append(f"Dimensiones: {e.info.width} x {e.info.height} x {e.info.bpp} bits")
            lines.append("Modificado: " + _dt.datetime.fromtimestamp(e.mtime).strftime("%d/%m/%Y %H:%M"))
            if e.desc:
                lines.append(e.desc)
            return "\n".join(lines)
        if role == Qt.UserRole:
            return e
        return None

    def flags(self, index):
        f = super().flags(index)
        if not index.isValid():
            return f | Qt.ItemIsDropEnabled
        f |= Qt.ItemIsDragEnabled
        if index.column() == 0:
            f |= Qt.ItemIsEditable
        if self.entries[index.row()].is_dir:
            f |= Qt.ItemIsDropEnabled
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or index.column() != 0:
            return False
        e = self.entries[index.row()]
        value = str(value).strip()
        if not value or value == e.name or "/" in value:
            return False
        new = os.path.join(os.path.dirname(e.path), value)
        if os.path.exists(new):
            self.renameFailed.emit(f"Ya existe un archivo llamado «{value}».")
            return False
        try:
            os.rename(e.path, new)
            fileops.move_description(e.path, new)
        except OSError as ex:
            self.renameFailed.emit(str(ex))
            return False
        old = e.path
        del self.index_of[old]
        e.path, e.name = new, value
        e.is_image = (not e.is_dir) and imaging.is_image(value)
        self.index_of[new] = index.row()
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLS) - 1))
        self.renamed.emit(old, new)
        return True

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        md = QMimeData()
        rows = sorted({i.row() for i in indexes})
        md.setUrls([QUrl.fromLocalFile(self.entries[r].path) for r in rows])
        return md

    def supportedDragActions(self):
        return Qt.CopyAction | Qt.MoveAction | Qt.LinkAction

    def supportedDropActions(self):
        return Qt.CopyAction | Qt.MoveAction

    # ---------------------------------------------------------------- miniaturas
    def set_thumb(self, path: str, img: QImage | None, info: ImageInfo):
        row = self.index_of.get(path)
        if row is None:
            return
        e = self.entries[row]
        e.info = info
        if img is not None:
            e.thumb = QPixmap.fromImage(img)
        else:
            e.thumb_failed = True
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLS) - 1))

    def clear_thumbs(self):
        for e in self.entries:
            e.thumb = None
            e.thumb_failed = False


# ============================================================ delegado de miniaturas

class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, view, browser):
        super().__init__(view)
        self.view = view
        self.browser = browser

    def cell_size(self) -> QSize:
        s = settings()
        ts = s.get("browser/thumb_size")
        lines = int(s.get("browser/thumb_show_name")) + int(s.get("browser/thumb_show_info"))
        fh = self.view.fontMetrics().height()
        return QSize(ts + 16, ts + 14 + lines * (fh + 1))

    def sizeHint(self, option, index):
        return self.cell_size()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index):
        e: Entry = index.data(Qt.UserRole)
        if e is None:
            return
        s = settings()
        ts = s.get("browser/thumb_size")
        r = option.rect.adjusted(2, 2, -2, -2)
        pal = option.palette
        selected = bool(option.state & QStyle.State_Selected)
        p.save()
        # marco de la miniatura (hundido, como en los visores clásicos)
        box = QRect(r.x() + (r.width() - (ts + 8)) // 2, r.y() + 2, ts + 8, ts + 8)
        p.fillRect(box, pal.color(QPalette.Base))
        if selected:
            p.fillRect(box, pal.color(QPalette.Highlight))
            inner = box.adjusted(3, 3, -3, -3)
            p.fillRect(inner, pal.color(QPalette.Base))
        p.setPen(pal.color(QPalette.Dark))
        p.drawLine(box.topLeft(), box.topRight())
        p.drawLine(box.topLeft(), box.bottomLeft())
        p.setPen(pal.color(QPalette.Light))
        p.drawLine(box.bottomLeft(), box.bottomRight())
        p.drawLine(box.topRight(), box.bottomRight())
        area = box.adjusted(4, 4, -4, -4)
        if e.thumb is not None:
            pm = e.thumb
            if pm.width() > area.width() or pm.height() > area.height():
                pm = pm.scaled(area.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = area.x() + (area.width() - pm.width()) // 2
            y = area.y() + (area.height() - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        else:
            ic = file_icon(e.path, e.is_dir)
            sz = min(48, ts) if not e.is_dir else min(64, ts)
            ic.paint(p, QRect(area.x() + (area.width() - sz) // 2, area.y() + (area.height() - sz) // 2, sz, sz))
            if e.is_image and not e.thumb_failed:
                self.browser.request_thumb(e.path)
        # texto
        y = box.bottom() + 3
        fh = option.fontMetrics.height()
        lines = []
        if s.get("browser/thumb_show_name"):
            lines.append(e.name)
        if s.get("browser/thumb_show_info"):
            lines.append(f"{e.info.width}x{e.info.height}x{e.info.bpp}b" if e.info and e.info.ok else
                         ("" if e.is_dir else kb_size(e.size)))
        for i, txt in enumerate(lines):
            tr = QRect(r.x(), y + i * (fh + 1), r.width(), fh)
            elided = option.fontMetrics.elidedText(txt, Qt.ElideMiddle, tr.width())
            if selected and i == 0:
                w = option.fontMetrics.horizontalAdvance(elided) + 6
                hl = QRect(tr.x() + (tr.width() - w) // 2, tr.y(), w, fh)
                p.fillRect(hl, pal.color(QPalette.Highlight))
                p.setPen(pal.color(QPalette.HighlightedText))
            else:
                p.setPen(pal.color(QPalette.Text))
            p.drawText(tr, Qt.AlignHCenter | Qt.AlignVCenter, elided)
        if option.state & QStyle.State_HasFocus:
            p.setPen(QPen(pal.color(QPalette.Text), 1, Qt.DotLine))
            p.drawRect(r.adjusted(0, 0, -1, -1))
        p.restore()

    def updateEditorGeometry(self, editor, option, index):
        ts = settings().get("browser/thumb_size")
        r = option.rect
        editor.setGeometry(QRect(r.x() + 2, r.y() + ts + 12, r.width() - 4, editor.sizeHint().height()))


# ============================================================ vistas con soltar archivos

class _DropMixin:
    browser = None

    def _setup_dnd(self, browser):
        self.browser = browser
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.setDropAction(Qt.CopyAction if e.modifiers() & Qt.ControlModifier else Qt.MoveAction)
            e.accept()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            return
        idx = self.indexAt(e.position().toPoint())
        target = self.browser.folder
        if idx.isValid():
            ent = idx.data(Qt.UserRole)
            if ent and ent.is_dir:
                target = ent.path
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        copy = bool(e.modifiers() & Qt.ControlModifier)
        if not copy and paths:
            # mover dentro del mismo sistema de archivos; copiar si es otro dispositivo
            try:
                copy = os.stat(paths[0]).st_dev != os.stat(target).st_dev
            except OSError:
                pass
        if any(os.path.dirname(p) == target for p in paths) and not copy:
            e.ignore()
            return
        e.setDropAction(Qt.CopyAction if copy else Qt.MoveAction)
        e.accept()
        QTimer.singleShot(0, lambda: self.browser.transfer(paths, target, move=not copy))


class FileListView(_DropMixin, QListView):
    pass


class FileTreeView(_DropMixin, QTreeView):
    pass


class FolderTree(QTreeView):
    """Árbol de carpetas que también acepta archivos soltados."""

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if e.mimeData().hasUrls() and idx.isValid():
            e.setDropAction(Qt.CopyAction if e.modifiers() & Qt.ControlModifier else Qt.MoveAction)
            e.accept()
        else:
            e.ignore()

    def dropEvent(self, e):
        idx = self.indexAt(e.position().toPoint())
        if not idx.isValid():
            return
        target = self.model().filePath(idx)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        copy = bool(e.modifiers() & Qt.ControlModifier)
        e.accept()
        QTimer.singleShot(0, lambda: self.browser.transfer(paths, target, move=not copy))


# ============================================================ vista previa

class _PrevSig(QObject):
    done = Signal(str, object)


class _PrevJob(QRunnable):
    def __init__(self, path, size, sig):
        super().__init__()
        self.path, self.size, self.sig = path, size, sig
        self.setAutoDelete(True)

    def run(self):
        img, _info = imaging.make_thumbnail(self.path, self.size, settings().get("general/exif_autorotate"))
        try:
            self.sig.done.emit(self.path, img)
        except RuntimeError:
            pass


class PreviewPane(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Sunken)
        self.setMinimumHeight(80)
        self.pix: QPixmap | None = None
        self.path = ""
        self.text = ""
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.sig = _PrevSig()
        self.sig.done.connect(self._done)

    def show_path(self, path: str | None):
        if path == self.path:
            return
        self.path = path or ""
        self.pix = None
        self.text = ""
        if path and imaging.is_image(path):
            self.pool.clear()
            self.pool.start(_PrevJob(path, 640, self.sig))
        elif path:
            self.text = os.path.basename(path)
        self.update()

    def _done(self, path, img):
        if path != self.path:
            return
        self.pix = QPixmap.fromImage(img) if img is not None else None
        if img is None:
            self.text = "Sin vista previa"
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        r = self.contentsRect().adjusted(4, 4, -4, -4)
        p.fillRect(self.contentsRect(), self.palette().color(QPalette.Base))
        if self.pix:
            pm = self.pix
            if pm.width() > r.width() or pm.height() > r.height():
                pm = pm.scaled(r.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(r.x() + (r.width() - pm.width()) // 2, r.y() + (r.height() - pm.height()) // 2, pm)
        elif self.text:
            p.setPen(self.palette().color(QPalette.Text))
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap, self.text)
        p.end()


# ============================================================ ventana principal

class BrowserWindow(QMainWindow):
    openViewer = Signal(list, int, bool)   # rutas, índice, presentación

    def __init__(self, folder: str | None = None):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.folder = ""
        self.history: list[str] = []
        self.hist_pos = -1
        self.loader = ThumbLoader(self)
        self.loader.thumbnail_ready.connect(self._thumb_ready)
        self._requested: set[str] = set()
        self.model = FolderModel(self)
        self.model.renameFailed.connect(lambda m: QMessageBox.warning(self, "Renombrar", m))
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(lambda _p: self._refresh_timer.start())
        self._refresh_timer = QTimer(self, singleShot=True, interval=400)
        self._refresh_timer.timeout.connect(lambda: self.refresh(keep_selection=True))

        self._build_ui()
        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self._build_status()
        self.reload_settings(first=True)

        s = settings()
        geo = s.value_raw("browser/geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1100, 760)
        st = s.value_raw("browser/state")
        if st:
            self.restoreState(st)
        sp = s.value_raw("browser/splitter")
        if sp:
            self.splitter.restoreState(sp)
        sp2 = s.value_raw("browser/splitter_left")
        if sp2:
            self.left_split.restoreState(sp2)

        start = folder or s.get("general/startup_folder") or s.get("general/last_folder") or str(Path.home())
        if not os.path.isdir(start):
            start = str(Path.home())
        self.navigate(start)

    # ------------------------------------------------------------------ interfaz
    def _build_ui(self):
        # Barra de dirección
        self.address = QComboBox()
        self.address.setEditable(True)
        self.address.setInsertPolicy(QComboBox.NoInsert)
        self.address.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.address.lineEdit().returnPressed.connect(lambda: self.navigate(os.path.expanduser(self.address.currentText())))
        self.address.activated.connect(lambda i: self.navigate(self.address.itemText(i)))

        # Árbol de carpetas
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setRootPath("")
        self.fs_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives)
        self.fs_model.setReadOnly(True)
        self.fs_model.directoryLoaded.connect(self._tree_loaded)
        self.tree = FolderTree(self)
        self.tree.setModel(self.fs_model)
        for c in range(1, 4):
            self.tree.hideColumn(c)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(False)
        self.tree.setIndentation(16)
        self.tree.clicked.connect(lambda idx: self.navigate(self.fs_model.filePath(idx)))
        self.tree.activated.connect(lambda idx: self.navigate(self.fs_model.filePath(idx)))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        # Favoritos
        self.fav_list = QListWidget()
        self.fav_list.itemActivated.connect(lambda it: self.navigate(it.data(Qt.UserRole)))
        self.fav_list.itemClicked.connect(lambda it: self.navigate(it.data(Qt.UserRole)))
        self.left_tabs = QTabWidget()
        self.left_tabs.setTabPosition(QTabWidget.South)
        self.left_tabs.addTab(self.tree, icon("folder"), "Carpetas")
        self.left_tabs.addTab(self.fav_list, icon("favorites"), "Favoritos")

        self.preview = PreviewPane()
        self.left_split = QSplitter(Qt.Vertical)
        self.left_split.addWidget(self.left_tabs)
        self.left_split.addWidget(self.preview)
        self.left_split.setStretchFactor(0, 3)
        self.left_split.setStretchFactor(1, 2)

        # Lista de archivos
        self.list_view = FileListView()
        self.list_view._setup_dnd(self)
        self.list_view.setModel(self.model)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setResizeMode(QListView.Adjust)
        self.list_view.setLayoutMode(QListView.Batched)
        self.list_view.setBatchSize(200)
        self.list_view.setMovement(QListView.Static)
        self.thumb_delegate = ThumbDelegate(self.list_view, self)
        self.plain_delegate = QStyledItemDelegate(self.list_view)

        self.details_view = FileTreeView()
        self.details_view._setup_dnd(self)
        self.details_view.setModel(self.model)
        self.details_view.setSelectionModel(self.list_view.selectionModel())
        self.details_view.setRootIsDecorated(False)
        self.details_view.setItemsExpandable(False)
        self.details_view.setUniformRowHeights(True)
        self.details_view.setAlternatingRowColors(False)
        self.details_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.details_view.setEditTriggers(QAbstractItemView.EditKeyPressed)
        self.details_view.setAllColumnsShowFocus(True)
        hdr = self.details_view.header()
        hdr.setSectionsClickable(True)
        hdr.setSortIndicatorShown(True)
        hdr.sectionClicked.connect(self._header_clicked)
        hdr.resizeSection(0, 260)
        hdr.resizeSection(1, 80)
        hdr.resizeSection(2, 140)
        hdr.resizeSection(3, 120)
        hdr.resizeSection(4, 140)

        for v in (self.list_view, self.details_view):
            v.activated.connect(self._activated)
            v.setContextMenuPolicy(Qt.CustomContextMenu)
            v.customContextMenuRequested.connect(self._item_menu)
            v.installEventFilter(self)
        self.list_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.list_view.selectionModel().currentChanged.connect(lambda cur, _prev: self._current_changed(cur))

        self.stack = QStackedWidget()
        self.stack.addWidget(self.list_view)
        self.stack.addWidget(self.details_view)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        rl.addWidget(self.stack)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.left_split)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 820])
        self.setCentralWidget(self.splitter)

    def _act(self, text, slot, shortcut=None, icon_name=None, checkable=False, tip=None):
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
            a.setData(icon_name)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(x) for x in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        return a

    def _build_actions(self):
        A = self._act
        # Archivo
        self.a_open = A("&Abrir...", self.open_file, "Ctrl+O", "open")
        self.a_new_folder = A("&Nueva carpeta...", self.new_folder, "Ctrl+N", "folder-new")
        self.a_acquire = A("Ad&quirir (escáner)...", self.acquire, None, "acquire")
        self.a_print = A("&Imprimir...", self.print_selected, "Ctrl+P", "print")
        self.a_props = A("P&ropiedades", self.properties, ["Alt+Return", "Alt+Enter"], "properties")
        self.a_exit = A("&Salir", self.close, "Ctrl+Q", "exit")
        # Edición
        self.a_cut = A("Cor&tar", lambda: self.clip_files(True), "Ctrl+X", "cut")
        self.a_copy = A("&Copiar", lambda: self.clip_files(False), "Ctrl+C", "copy")
        self.a_paste = A("&Pegar", self.paste_files, "Ctrl+V", "paste")
        self.a_delete = A("&Eliminar", lambda: self.delete_selected(False), "Delete", "delete")
        self.a_delete_perm = A("Eliminar &definitivamente", lambda: self.delete_selected(True), "Shift+Delete")
        self.a_rename = A("Re&nombrar", self.rename, "F2", "rename")
        self.a_rename_series = A("Renombrar en &serie...", self.rename_series, "Ctrl+Shift+R")
        self.a_copy_to = A("Copiar &a carpeta...", lambda: self.copy_move_to(False), "Ctrl+Shift+M", "copy-to")
        self.a_move_to = A("&Mover a carpeta...", lambda: self.copy_move_to(True), "Ctrl+M", "move-to")
        self.a_select_all = A("Seleccionar &todo", lambda: self.current_view().selectAll(), "Ctrl+A")
        self.a_select_images = A("Seleccionar sólo &imágenes", self.select_images, "Ctrl+Shift+A")
        self.a_invert = A("&Invertir selección", self.invert_selection, "Ctrl+I")
        self.a_select_none = A("&Anular selección", lambda: self.current_view().clearSelection())
        self.a_desc = A("&Descripción...", self.edit_description, "Ctrl+Shift+E")
        # Ver
        self.a_tb = A("&Barra de herramientas", self._toggle_toolbar, None, checkable=True)
        self.a_sb = A("Barra de &estado", self._toggle_statusbar, None, checkable=True)
        self.a_tree = A("Árbol de &carpetas", self._toggle_tree, "F9", "tree", checkable=True)
        self.a_prev_pane = A("&Vista previa", self._toggle_preview, "F8", "preview", checkable=True)
        self.mode_group = QActionGroup(self)
        self.a_modes = {}
        for i, (key, txt, ic) in enumerate((("thumbs", "&Miniaturas", "thumbs"), ("large", "Iconos &grandes", None),
                                            ("small", "Iconos &pequeños", None), ("list", "&Lista", "list"),
                                            ("details", "&Detalles", "details"))):
            a = A(txt, lambda _c=False, k=key: self.set_mode(k), f"Ctrl+{i + 1}", ic, checkable=True)
            self.mode_group.addAction(a)
            self.a_modes[key] = a
        self.sort_group = QActionGroup(self)
        self.a_sorts = {}
        for key, txt in (("name", "por &nombre"), ("ext", "por &tipo (extensión)"), ("size", "por t&amaño"),
                         ("date", "por &fecha"), ("props", "por &propiedades de imagen")):
            a = A(txt, lambda _c=False, k=key: self.set_sort(k), None, None, checkable=True)
            self.sort_group.addAction(a)
            self.a_sorts[key] = a
        self.a_sort_desc = A("Orden &descendente", self._toggle_sort_desc, None, checkable=True)
        self.a_filter = A("&Filtro...", self.filter_dialog, None, "filter")
        self.a_refresh = A("&Actualizar", lambda: self.refresh(keep_selection=True), "F5", "refresh")
        self.a_dark = A("Modo &oscuro", self.toggle_dark, "Ctrl+Shift+D", "dark", checkable=True)
        self.a_fullscreen = A("Pantalla &completa", self._toggle_fs, "F11", "fullscreen")
        # Ir
        self.a_back = A("&Atrás", self.go_back, "Alt+Left", "back")
        self.a_forward = A("A&delante", self.go_forward, "Alt+Right", "forward")
        self.a_up = A("Carpeta &superior", self.go_up, ["Backspace", "Alt+Up"], "up")
        self.a_home = A("Carpeta &personal", lambda: self.navigate(str(Path.home())), "Alt+Home", "home")
        self.a_goto = A("&Ir a carpeta...", self.goto_folder, ["Ctrl+G", "Ctrl+L"])
        self.a_view = A("&Ver imagen", self.view_selected, None, "view", tip="Ver la imagen seleccionada (Intro)")
        self.a_slideshow = A("&Presentación", self.slideshow, "Ctrl+S", "slideshow")
        # Favoritos
        self.a_fav_add = A("&Añadir carpeta a favoritos", self.add_favorite, "Ctrl+D", "favorites")
        self.a_fav_org = A("&Organizar favoritos...", self.organize_favorites)
        # Herramientas
        self.a_convert = A("&Convertir formato...", self.convert, "Ctrl+F", "convert")
        self.a_rotate = A("&Rotar / voltear...", self.rotate, "Ctrl+R", "rotate-right")
        self.a_rot_l = A("Rotar a la izquierda (sin pérdida)", lambda: self.quick_rotate("270"), "Ctrl+[", "rotate-left")
        self.a_rot_r = A("Rotar a la derecha (sin pérdida)", lambda: self.quick_rotate("90"), "Ctrl+]", "rotate-right")
        self.a_resize = A("Redimen&sionar...", self.resize_images, "Ctrl+Shift+Z", "resize")
        self.a_mozopt = A("Optimizar JPEG con &MozJPEG (sin pérdida)...", self.moz_optimize)
        self.a_wp = {m: A(t, lambda _c=False, mm=m: self.set_wallpaper(mm)) for m, t in
                     (("centered", "&Centrado"), ("tiled", "&Mosaico"), ("zoom", "&Ajustado (llenar)"),
                      ("stretched", "&Estirado"))}
        self.a_wp_restore = A("&Restaurar fondo anterior", self.restore_wallpaper)
        self.a_edit = A("&Editar con programa externo", self.edit_external, "Ctrl+E", "edit")
        self.a_open_default = A("Abrir con la aplicación &predeterminada", self.open_default, "Ctrl+Shift+O")
        self.a_open_fm = A("Abrir en el &gestor de archivos", self.open_file_manager)
        self.a_terminal = A("Abrir &terminal aquí", lambda: fileops.open_terminal(self.folder), None, "terminal")
        self.a_options = A("&Opciones...", self.options, "Alt+O", "options")
        self.a_integrate = A("&Integrar en el menú de aplicaciones", self.integrate)
        self.a_default = A("Establecer como &visor predeterminado de imágenes", self.make_default)
        # Ayuda
        self.a_shortcuts = A("&Atajos de teclado", lambda: dialogs.ShortcutsDialog(self).exec(), "F1", "help")
        self.a_about = A("&Acerca de RetroVisor", lambda: dialogs.about(self), None, "info")
        for a in self.findChildren(QAction):
            self.addAction(a)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Archivo")
        for a in (self.a_open, self.a_new_folder, None, self.a_acquire, self.a_print, None, self.a_props, None):
            m.addSeparator() if a is None else m.addAction(a)
        self.recent_menu = m.addMenu("Carpetas &recientes")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        m.addSeparator()
        m.addAction(self.a_exit)

        m = mb.addMenu("&Edición")
        for a in (self.a_cut, self.a_copy, self.a_paste, None, self.a_delete, self.a_delete_perm, self.a_rename,
                  self.a_rename_series, None, self.a_copy_to, self.a_move_to, None, self.a_select_all,
                  self.a_select_images, self.a_invert, self.a_select_none, None, self.a_desc):
            m.addSeparator() if a is None else m.addAction(a)
        m.aboutToShow.connect(self._update_actions)

        m = mb.addMenu("&Ver")
        for a in (self.a_tb, self.a_sb, self.a_tree, self.a_prev_pane, None):
            m.addSeparator() if a is None else m.addAction(a)
        mm = m.addMenu("&Modo")
        for a in self.a_modes.values():
            mm.addAction(a)
        sm = m.addMenu("&Ordenar")
        for a in self.a_sorts.values():
            sm.addAction(a)
        sm.addSeparator()
        sm.addAction(self.a_sort_desc)
        tm = m.addMenu("&Tamaño de miniaturas")
        self.size_group = QActionGroup(self)
        self.a_sizes = {}
        for px in (48, 64, 80, 96, 120, 160, 200, 256, 320):
            a = QAction(f"{px} x {px}", self, checkable=True)
            a.triggered.connect(lambda _c=False, v=px: self.set_thumb_size(v))
            self.size_group.addAction(a)
            tm.addAction(a)
            self.a_sizes[px] = a
        m.addAction(self.a_filter)
        m.addSeparator()
        m.addAction(self.a_dark)
        m.addAction(self.a_fullscreen)
        m.addSeparator()
        m.addAction(self.a_refresh)

        m = mb.addMenu("&Ir")
        for a in (self.a_back, self.a_forward, self.a_up, self.a_home, self.a_goto, None, self.a_view,
                  self.a_slideshow):
            m.addSeparator() if a is None else m.addAction(a)

        self.fav_menu = mb.addMenu("&Favoritos")
        self.fav_menu.aboutToShow.connect(self._fill_fav_menu)

        m = mb.addMenu("&Herramientas")
        for a in (self.a_convert, self.a_rotate, self.a_rot_l, self.a_rot_r, self.a_resize, self.a_mozopt, None):
            m.addSeparator() if a is None else m.addAction(a)
        wm = m.addMenu(icon("wallpaper"), "&Fondo de escritorio")
        for a in self.a_wp.values():
            wm.addAction(a)
        wm.addSeparator()
        wm.addAction(self.a_wp_restore)
        m.addSeparator()
        for a in (self.a_edit, self.a_open_default, self.a_open_fm, self.a_terminal, None, self.a_integrate,
                  self.a_default, None, self.a_options):
            m.addSeparator() if a is None else m.addAction(a)
        m.aboutToShow.connect(self._update_actions)

        m = mb.addMenu("Ay&uda")
        m.addAction(self.a_shortcuts)
        m.addSeparator()
        m.addAction(self.a_about)

    def _build_toolbar(self):
        tb = QToolBar("Barra de herramientas", self)
        tb.setObjectName("browser_toolbar")
        tb.setIconSize(QSize(24, 24))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for a in (self.a_view, None, self.a_back, self.a_forward, self.a_up, None, self.a_tree, self.a_prev_pane,
                  None):
            tb.addSeparator() if a is None else tb.addAction(a)
        # botón de modo con menú desplegable
        mode_btn = QToolButton()
        mode_btn.setPopupMode(QToolButton.MenuButtonPopup)
        mode_menu = QMenu(mode_btn)
        for a in self.a_modes.values():
            mode_menu.addAction(a)
        mode_btn.setMenu(mode_menu)
        mode_btn.setDefaultAction(self.a_modes["thumbs"])
        mode_btn.setToolTip("Modo de vista")
        self.mode_btn = mode_btn
        tb.addWidget(mode_btn)
        sort_btn = QToolButton()
        sort_btn.setIcon(icon("sort"))
        sort_btn.setToolTip("Ordenar")
        sort_btn.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(sort_btn)
        for a in self.a_sorts.values():
            sm.addAction(a)
        sm.addSeparator()
        sm.addAction(self.a_sort_desc)
        sort_btn.setMenu(sm)
        self.sort_btn = sort_btn
        tb.addWidget(sort_btn)
        tb.addSeparator()
        for a in (self.a_copy_to, self.a_move_to, self.a_delete, self.a_rename, self.a_props, None,
                  self.a_convert, self.a_rotate, self.a_slideshow, self.a_print, None, self.a_dark):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.toolbar = tb
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.addToolBarBreak()
        tb2 = QToolBar("Dirección", self)
        tb2.setObjectName("address_toolbar")
        tb2.addWidget(QLabel(" Dirección: "))
        tb2.addWidget(self.address)
        self.address_bar = tb2
        self.addToolBar(Qt.TopToolBarArea, tb2)

    def _build_status(self):
        sb = self.statusBar()
        self.st_total = QLabel()
        self.st_sel = QLabel()
        self.st_info = QLabel()
        self.st_date = QLabel()
        for w in (self.st_total, self.st_sel, self.st_info, self.st_date):
            w.setFrameShape(QLabel.Panel)
            w.setFrameShadow(QLabel.Sunken)
            w.setContentsMargins(4, 0, 4, 0)
        sb.addWidget(self.st_total, 2)
        sb.addWidget(self.st_sel, 2)
        sb.addWidget(self.st_info, 3)
        sb.addWidget(self.st_date, 2)

    # ------------------------------------------------------------------ ajustes
    def reload_settings(self, first: bool = False):
        s = settings()
        self.a_tb.setChecked(s.get("browser/show_toolbar"))
        self.toolbar.setVisible(s.get("browser/show_toolbar"))
        self.a_sb.setChecked(s.get("browser/show_statusbar"))
        self.statusBar().setVisible(s.get("browser/show_statusbar"))
        self.a_tree.setChecked(s.get("browser/show_tree"))
        self.left_tabs.setVisible(s.get("browser/show_tree"))
        self.a_prev_pane.setChecked(s.get("browser/show_preview"))
        self.preview.setVisible(s.get("browser/show_preview"))
        self._update_left_visibility()
        self.a_sorts.get(s.get("browser/sort_key"), self.a_sorts["name"]).setChecked(True)
        self.a_sort_desc.setChecked(s.get("browser/sort_desc"))
        ts = s.get("browser/thumb_size")
        if ts in self.a_sizes:
            self.a_sizes[ts].setChecked(True)
        self.a_dark.setChecked(is_dark())
        self.fs_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot | QDir.Drives |
                                (QDir.Hidden if s.get("browser/show_hidden") else QDir.Filter(0)))
        if ts != self.model.thumb_size:
            self.model.thumb_size = ts
            self.model.clear_thumbs()
            self._requested.clear()
            self.loader.reset()
        self.set_mode(s.get("browser/mode"), save=False)
        self._fill_fav_list()
        if not first and self.folder:
            self.refresh(keep_selection=True)

    def on_theme_changed(self):
        for a in self.findChildren(QAction):
            name = a.data()
            if isinstance(name, str) and name:
                a.setIcon(icon(name))
        self.sort_btn.setIcon(icon("sort"))
        self.left_tabs.setTabIcon(0, icon("folder"))
        self.left_tabs.setTabIcon(1, icon("favorites"))
        self.a_dark.setChecked(is_dark())
        _mime_icons.clear()
        self.current_view().viewport().update()

    def _update_left_visibility(self):
        self.left_split.setVisible(self.left_tabs.isVisibleTo(self) or self.preview.isVisibleTo(self)
                                   or (self.a_tree.isChecked() or self.a_prev_pane.isChecked()))
        self.left_tabs.setVisible(self.a_tree.isChecked())
        self.preview.setVisible(self.a_prev_pane.isChecked())
        self.left_split.setVisible(self.a_tree.isChecked() or self.a_prev_pane.isChecked())

    # ------------------------------------------------------------------ modos de vista
    def current_view(self) -> QAbstractItemView:
        return self.stack.currentWidget()

    def set_mode(self, mode: str, save: bool = True):
        if mode not in self.a_modes:
            mode = "thumbs"
        self.a_modes[mode].setChecked(True)
        self.mode_btn.setDefaultAction(self.a_modes[mode])
        lv = self.list_view
        if save:
            settings().set("browser/mode", mode)
        if mode == "details":
            self.stack.setCurrentWidget(self.details_view)
            self.details_view.setIconSize(QSize(16, 16))
            self._request_visible_info()
            return
        self.stack.setCurrentWidget(lv)
        lv.setModelColumn(0)
        if mode == "thumbs":
            lv.setViewMode(QListView.IconMode)
            lv.setFlow(QListView.LeftToRight)
            lv.setWrapping(True)
            lv.setItemDelegate(self.thumb_delegate)
            cs = self.thumb_delegate.cell_size()
            lv.setGridSize(QSize(cs.width() + 6, cs.height() + 6))
            lv.setSpacing(0)
            lv.setWordWrap(False)
        elif mode == "large":
            lv.setItemDelegate(self.plain_delegate)
            lv.setViewMode(QListView.IconMode)
            lv.setFlow(QListView.LeftToRight)
            lv.setWrapping(True)
            lv.setIconSize(QSize(32, 32))
            lv.setGridSize(QSize(96, 72))
            lv.setWordWrap(True)
        elif mode == "small":
            lv.setItemDelegate(self.plain_delegate)
            lv.setViewMode(QListView.ListMode)
            lv.setFlow(QListView.LeftToRight)
            lv.setWrapping(True)
            lv.setIconSize(QSize(16, 16))
            lv.setGridSize(QSize(180, 22))
            lv.setWordWrap(False)
        else:  # list
            lv.setItemDelegate(self.plain_delegate)
            lv.setViewMode(QListView.ListMode)
            lv.setFlow(QListView.TopToBottom)
            lv.setWrapping(True)
            lv.setIconSize(QSize(16, 16))
            lv.setGridSize(QSize())
            lv.setSpacing(1)
            lv.setWordWrap(False)
        lv.setMovement(QListView.Static)
        lv.setDragDropMode(QAbstractItemView.DragDrop)
        lv.doItemsLayout()

    def set_thumb_size(self, px: int):
        settings().set("browser/thumb_size", px)
        self.reload_settings()

    def set_sort(self, key: str):
        settings().set("browser/sort_key", key)
        self._resort()

    def _toggle_sort_desc(self):
        settings().set("browser/sort_desc", self.a_sort_desc.isChecked())
        self._resort()

    def _resort(self):
        self.model.resort()
        cur = self.list_view.currentIndex()
        if cur.isValid():
            self.current_view().scrollTo(cur)

    def _header_clicked(self, section: int):
        key = {0: "name", 1: "size", 2: "ext", 3: "date", 4: "props"}.get(section, "name")
        s = settings()
        if s.get("browser/sort_key") == key:
            s.set("browser/sort_desc", not s.get("browser/sort_desc"))
        else:
            s.set("browser/sort_key", key)
            s.set("browser/sort_desc", False)
        self.a_sorts[key].setChecked(True)
        self.a_sort_desc.setChecked(s.get("browser/sort_desc"))
        self.details_view.header().setSortIndicator(section, Qt.DescendingOrder if s.get("browser/sort_desc") else Qt.AscendingOrder)
        self._resort()

    def _toggle_toolbar(self):
        settings().set("browser/show_toolbar", self.a_tb.isChecked())
        self.toolbar.setVisible(self.a_tb.isChecked())

    def _toggle_statusbar(self):
        settings().set("browser/show_statusbar", self.a_sb.isChecked())
        self.statusBar().setVisible(self.a_sb.isChecked())

    def _toggle_tree(self):
        settings().set("browser/show_tree", self.a_tree.isChecked())
        self._update_left_visibility()

    def _toggle_preview(self):
        settings().set("browser/show_preview", self.a_prev_pane.isChecked())
        self._update_left_visibility()
        if self.a_prev_pane.isChecked():
            self.preview.path = ""
            self._current_changed(self.list_view.currentIndex())

    def _toggle_fs(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def toggle_dark(self):
        settings().set("appearance/theme", "dark" if self.a_dark.isChecked() else "light")
        apply_theme()

    # ------------------------------------------------------------------ navegación
    def navigate(self, folder: str, add_history: bool = True, select: str | None = None):
        folder = os.path.abspath(os.path.expanduser(folder))
        if not os.path.isdir(folder):
            if os.path.isfile(folder):
                select = folder
                folder = os.path.dirname(folder)
            else:
                QMessageBox.warning(self, "RetroVisor", f"No se encuentra la carpeta:\n{folder}")
                self.address.setEditText(self.folder)
                return
        if not os.access(folder, os.R_OK | os.X_OK):
            QMessageBox.warning(self, "RetroVisor", f"No tiene permiso para abrir:\n{folder}")
            return
        if self.folder and self.folder in self.watcher.directories():
            self.watcher.removePath(self.folder)
        self.folder = folder
        if add_history:
            self.history = self.history[: self.hist_pos + 1]
            if not self.history or self.history[-1] != folder:
                self.history.append(folder)
            self.hist_pos = len(self.history) - 1
        settings().set("general/last_folder", folder)
        recent = [f for f in settings().get("browser/recent_folders", []) if f != folder]
        settings().set("browser/recent_folders", [folder] + recent[:14])
        self.watcher.addPath(folder)
        self._load_folder(select)
        self.address.blockSignals(True)
        if self.address.findText(folder) < 0:
            self.address.insertItem(0, folder)
            while self.address.count() > 20:
                self.address.removeItem(self.address.count() - 1)
        self.address.setCurrentIndex(self.address.findText(folder))
        self.address.blockSignals(False)
        self._sync_tree()
        self.setWindowTitle(f"RetroVisor - {folder}")
        self.a_back.setEnabled(self.hist_pos > 0)
        self.a_forward.setEnabled(self.hist_pos < len(self.history) - 1)
        self.a_up.setEnabled(folder != "/")

    def _load_folder(self, select: str | None = None, keep: list[str] | None = None, keep_current: str | None = None):
        self.loader.reset()
        self._requested.clear()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.model.load(self.folder)
        finally:
            QApplication.restoreOverrideCursor()
        view = self.current_view()
        sm = self.list_view.selectionModel()
        if keep:
            sel = QItemSelection()
            for p in keep:
                r = self.model.index_of.get(p)
                if r is not None:
                    sel.select(self.model.index(r, 0), self.model.index(r, len(COLS) - 1))
            sm.select(sel, QItemSelectionModel.ClearAndSelect)
        if keep_current and keep_current in self.model.index_of:
            sm.setCurrentIndex(self.model.index(self.model.index_of[keep_current], 0), QItemSelectionModel.NoUpdate)
        if select and select in self.model.index_of:
            idx = self.model.index(self.model.index_of[select], 0)
            sm.setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            QTimer.singleShot(0, lambda: view.scrollTo(idx, QAbstractItemView.PositionAtCenter))
        elif not keep and self.model.rowCount() and not keep_current:
            view.scrollToTop()
            sm.setCurrentIndex(self.model.index(0, 0), QItemSelectionModel.NoUpdate)
        if self.stack.currentWidget() is self.details_view:
            self._request_visible_info()
        self._update_status()
        if not select and not keep:
            self.preview.show_path(None)

    def refresh(self, keep_selection: bool = False):
        if not self.folder:
            return
        if not os.path.isdir(self.folder):
            self.go_up()
            return
        keep = self.selected_paths() if keep_selection else None
        cur = self.list_view.currentIndex()
        cur_path = cur.data(Qt.UserRole).path if cur.isValid() and cur.data(Qt.UserRole) else None
        self._load_folder(keep=keep, keep_current=cur_path)

    def go_back(self):
        if self.hist_pos > 0:
            self.hist_pos -= 1
            self.navigate(self.history[self.hist_pos], add_history=False)

    def go_forward(self):
        if self.hist_pos < len(self.history) - 1:
            self.hist_pos += 1
            self.navigate(self.history[self.hist_pos], add_history=False)

    def go_up(self):
        parent = os.path.dirname(self.folder.rstrip("/")) or "/"
        if parent != self.folder:
            self.navigate(parent, select=self.folder)

    def goto_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ir a carpeta", self.folder)
        if d:
            self.navigate(d)

    def select_file(self, path: str):
        """Llamado al volver del visor: muestra la carpeta y selecciona la imagen."""
        if not path:
            return
        folder = os.path.dirname(path)
        if folder != self.folder:
            self.navigate(folder, select=path)
        else:
            self.refresh()
            r = self.model.index_of.get(path)
            if r is not None:
                idx = self.model.index(r, 0)
                self.list_view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
                self.current_view().scrollTo(idx, QAbstractItemView.PositionAtCenter)

    # ------------------------------------------------------------------ miniaturas
    def request_thumb(self, path: str):
        if path in self._requested:
            return
        self._requested.add(path)
        s = settings()
        self.loader.request([path], s.get("browser/thumb_size"), s.get("browser/thumb_cache"),
                            s.get("general/exif_autorotate"))

    def _request_visible_info(self):
        # En modo detalles, pedir información de todas las imágenes de la carpeta (en orden)
        for e in self.model.entries:
            if e.is_image and e.info is None:
                self.request_thumb(e.path)

    def _thumb_ready(self, path, img, info):
        self.model.set_thumb(path, img, info)
        cur = self.list_view.currentIndex()
        if cur.isValid() and cur.data(Qt.UserRole) and cur.data(Qt.UserRole).path == path:
            self._update_status()

    # ------------------------------------------------------------------ selección
    def selected_entries(self) -> list[Entry]:
        rows = sorted({i.row() for i in self.list_view.selectionModel().selectedIndexes()})
        return [self.model.entries[r] for r in rows if r < len(self.model.entries)]

    def selected_paths(self) -> list[str]:
        return [e.path for e in self.selected_entries()]

    def selected_images(self) -> list[str]:
        return [e.path for e in self.selected_entries() if e.is_image]

    def _selection_changed(self, *_):
        self._update_status()

    def _current_changed(self, cur):
        e = cur.data(Qt.UserRole) if cur.isValid() else None
        if self.preview.isVisible():
            self.preview.show_path(e.path if e else None)
        self._update_status()

    def select_images(self):
        sel = QItemSelection()
        for i, e in enumerate(self.model.entries):
            if e.is_image:
                sel.select(self.model.index(i, 0), self.model.index(i, len(COLS) - 1))
        self.list_view.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def invert_selection(self):
        sel = QItemSelection(self.model.index(0, 0), self.model.index(self.model.rowCount() - 1, len(COLS) - 1))
        self.list_view.selectionModel().select(sel, QItemSelectionModel.Toggle)

    def _update_status(self):
        n_files = sum(1 for e in self.model.entries if not e.is_dir)
        total = sum(e.size for e in self.model.entries)
        self.st_total.setText(f"Total {n_files} archivos ({kb_size(total)})")
        sel = self.selected_entries()
        if sel:
            self.st_sel.setText(f"{len(sel)} seleccionados ({kb_size(sum(e.size for e in sel))})")
        else:
            self.st_sel.setText("")
        cur = self.list_view.currentIndex()
        e = cur.data(Qt.UserRole) if cur.isValid() else None
        if e is not None:
            info = f"{e.name}"
            if e.info and e.info.ok:
                info += f"  {e.info.width}x{e.info.height}x{e.info.bpp}b"
            self.st_info.setText(info)
            self.st_date.setText(_dt.datetime.fromtimestamp(e.mtime).strftime("Modificado: %d/%m/%Y %H:%M"))
        else:
            self.st_info.setText("")
            self.st_date.setText("")

    def _update_actions(self):
        has = bool(self.selected_paths())
        imgs = bool(self.selected_images())
        for a in (self.a_cut, self.a_copy, self.a_delete, self.a_delete_perm, self.a_rename, self.a_copy_to,
                  self.a_move_to, self.a_rename_series, self.a_desc):
            a.setEnabled(has)
        for a in (self.a_convert, self.a_rotate, self.a_resize, self.a_edit, self.a_rot_l, self.a_rot_r,
                  *self.a_wp.values()):
            a.setEnabled(imgs)
        paths, _cut = fileops.get_file_clipboard()
        self.a_paste.setEnabled(bool(paths) or not QApplication.clipboard().image().isNull())

    # ------------------------------------------------------------------ eventos
    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent

        if ev.type() == QEvent.KeyPress and obj in (self.list_view, self.details_view):
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and not ev.modifiers() & Qt.AltModifier:
                if obj.state() != QAbstractItemView.EditingState:
                    self._activated(obj.currentIndex())
                    return True
        return super().eventFilter(obj, ev)

    def _activated(self, idx):
        if not idx.isValid():
            return
        e: Entry = idx.data(Qt.UserRole)
        if e.is_dir:
            self.navigate(e.path)
        elif e.is_image:
            self.view_images(e.path)
        else:
            fileops.open_with_default(e.path)

    def image_list(self) -> list[str]:
        return [e.path for e in self.model.entries if e.is_image]

    def view_images(self, start_path: str | None = None, slideshow: bool = False):
        sel = self.selected_images()
        if len(sel) > 1 and (start_path is None or start_path in sel):
            paths = sel  # varias imágenes seleccionadas: ver sólo esas
        else:
            paths = self.image_list()
        if not paths:
            return
        idx = paths.index(start_path) if start_path in paths else 0
        self.openViewer.emit(paths, idx, slideshow)

    def view_selected(self):
        cur = self.list_view.currentIndex()
        e = cur.data(Qt.UserRole) if cur.isValid() else None
        if e and not e.is_image:
            self._activated(cur)
            return
        self.view_images(e.path if e else None)

    def slideshow(self):
        cur = self.list_view.currentIndex()
        e = cur.data(Qt.UserRole) if cur.isValid() else None
        self.view_images(e.path if e and e.is_image else None, slideshow=True)

    def _item_menu(self, pos):
        view = self.current_view()
        idx = view.indexAt(pos)
        if idx.isValid() and not view.selectionModel().isSelected(idx):
            view.selectionModel().setCurrentIndex(idx, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self._update_actions()
        m = QMenu(self)
        if idx.isValid():
            e: Entry = idx.data(Qt.UserRole)
            if e.is_dir:
                m.addAction(icon("folder"), "&Abrir", lambda: self.navigate(e.path))
            else:
                m.addAction(self.a_view)
            m.addAction(self.a_edit)
            m.addAction(self.a_open_default)
            m.addSeparator()
            m.addAction(self.a_cut)
            m.addAction(self.a_copy)
        m.addAction(self.a_paste)
        m.addSeparator()
        if idx.isValid():
            m.addAction(self.a_copy_to)
            m.addAction(self.a_move_to)
            m.addAction(self.a_rename)
            m.addAction(self.a_delete)
            m.addSeparator()
            if self.selected_images():
                m.addAction(self.a_rot_l)
                m.addAction(self.a_rot_r)
                m.addAction(self.a_convert)
                m.addAction(self.a_resize)
                wm = m.addMenu(icon("wallpaper"), "&Fondo de escritorio")
                for a in self.a_wp.values():
                    wm.addAction(a)
                m.addSeparator()
            m.addAction(self.a_desc)
            m.addAction(self.a_props)
        else:
            mm = m.addMenu("&Modo")
            for a in self.a_modes.values():
                mm.addAction(a)
            sm = m.addMenu("&Ordenar")
            for a in self.a_sorts.values():
                sm.addAction(a)
            sm.addSeparator()
            sm.addAction(self.a_sort_desc)
            m.addAction(self.a_refresh)
            m.addSeparator()
            m.addAction(self.a_new_folder)
            m.addAction(self.a_select_all)
            m.addAction(self.a_terminal)
        m.exec(view.viewport().mapToGlobal(pos))

    def _sync_tree(self):
        idx = self.fs_model.index(self.folder)
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
            self.tree.scrollTo(idx, QAbstractItemView.PositionAtCenter)

    def _tree_loaded(self, path: str):
        # QFileSystemModel carga las carpetas en segundo plano: al terminar, mostrar la actual.
        if self.folder == path or self.folder.startswith(path.rstrip("/") + "/"):
            QTimer.singleShot(0, self._sync_tree)

    def _tree_menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        path = self.fs_model.filePath(idx)
        m = QMenu(self)
        m.addAction(icon("folder"), "&Abrir", lambda: self.navigate(path))
        m.addAction(icon("favorites"), "Añadir a &favoritos", lambda: self.add_favorite(path))
        m.addAction("Abrir en el &gestor de archivos", lambda: fileops.open_in_file_manager(path))
        m.addAction(icon("terminal"), "Abrir &terminal aquí", lambda: fileops.open_terminal(path))
        m.addSeparator()
        m.addAction(icon("folder-new"), "&Nueva carpeta...", lambda: self.new_folder(path))
        m.addAction(icon("rename"), "Re&nombrar...", lambda: self._rename_folder(path))
        m.addAction(icon("delete"), "&Eliminar", lambda: self._delete_paths([path], False))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ archivo
    def open_file(self):
        exts = " ".join("*" + e for e in sorted(imaging.IMAGE_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "Abrir", self.folder,
                                              f"Imágenes ({exts});;Todos los archivos (*)")
        if path:
            self.navigate(os.path.dirname(path), select=path)
            if imaging.is_image(path):
                self.view_images(path)

    def new_folder(self, parent: str | None = None):
        parent = parent or self.folder
        name, ok = QInputDialog.getText(self, "Nueva carpeta", "Nombre de la carpeta:", text="Nueva carpeta")
        if not ok or not name.strip():
            return
        path = os.path.join(parent, name.strip())
        try:
            os.mkdir(path)
        except OSError as e:
            QMessageBox.critical(self, "Nueva carpeta", str(e))
            return
        if parent == self.folder:
            self._load_folder(select=path)

    def _rename_folder(self, path):
        name, ok = QInputDialog.getText(self, "Renombrar carpeta", "Nuevo nombre:", text=os.path.basename(path))
        if ok and name and name != os.path.basename(path):
            new = os.path.join(os.path.dirname(path), name)
            try:
                os.rename(path, new)
            except OSError as e:
                QMessageBox.critical(self, "Renombrar", str(e))
                return
            if self.folder == path or self.folder.startswith(path + "/"):
                self.navigate(new + self.folder[len(path):])
            else:
                self.refresh(True)

    def acquire(self):
        if not fileops.acquire_scanner():
            QMessageBox.information(self, "Adquirir", "No se encontró ningún programa de escaneo.\n"
                                                      "Instale «simple-scan» (Escáner de documentos) desde el "
                                                      "Gestor de software de Linux Mint.")

    def print_selected(self):
        paths = self.selected_images()
        if not paths:
            return
        items = []
        for p in paths:
            try:
                li = imaging.load_image(p, autorotate=settings().get("general/exif_autorotate"))
                dpi = float(li.dpi[0]) if li.dpi else 96.0
                items.append((li.qimage, os.path.basename(p), dpi))
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Imprimir", f"{os.path.basename(p)}: {e}")
        dialogs.print_images(self, items)

    def properties(self):
        paths = self.selected_paths()
        if not paths:
            QMessageBox.information(self, "Propiedades", f"Carpeta: {self.folder}\n"
                                    f"Archivos: {sum(1 for e in self.model.entries if not e.is_dir)}")
            return
        if os.path.isdir(paths[0]):
            n = sum(len(f) for _r, _d, f in os.walk(paths[0]))
            QMessageBox.information(self, "Propiedades", f"Carpeta: {paths[0]}\nContiene {n} archivos.")
            return
        if dialogs.PropertiesDialog(paths[0], None, self).exec() == QDialog.Accepted:
            self.refresh(keep_selection=True)

    def edit_description(self):
        paths = self.selected_paths()
        if not paths:
            return
        cur = fileops.read_descriptions(self.folder).get(os.path.basename(paths[0]).lower(), "")
        text, ok = QInputDialog.getText(self, "Descripción", f"Descripción de {len(paths)} archivo(s):", text=cur)
        if ok:
            for p in paths:
                fileops.write_description(p, text)
            self.refresh(keep_selection=True)

    # ------------------------------------------------------------------ edición
    def clip_files(self, cut: bool):
        paths = self.selected_paths()
        if paths:
            fileops.set_file_clipboard(paths, cut)
            self.statusBar().showMessage(f"{len(paths)} elemento(s) {'cortados' if cut else 'copiados'}", 3000)

    def paste_files(self):
        paths, cut = fileops.get_file_clipboard()
        if paths:
            self.transfer(paths, self.folder, move=cut)
            if cut:
                QApplication.clipboard().clear()
            return
        img = QApplication.clipboard().image()
        if not img.isNull():
            from .util import unique_path

            dest = str(unique_path(Path(self.folder) / "Pegado.png"))
            name, ok = QInputDialog.getText(self, "Pegar imagen", "Guardar la imagen del portapapeles como:",
                                            text=os.path.basename(dest))
            if ok and name:
                dest = os.path.join(self.folder, name)
                fmt = imaging.EXT_TO_SAVE.get(Path(dest).suffix.lower(), "PNG")
                if fmt == "PNG" and not dest.lower().endswith(".png"):
                    dest += ".png"
                imaging.save_image(imaging.qimage_to_pil(img), dest, fmt, {})
                self._load_folder(select=dest)

    def transfer(self, paths: list[str], target: str, move: bool):
        paths = [p for p in paths if os.path.exists(p)]
        if not paths:
            return
        target = os.path.abspath(target)
        for p in paths:
            if os.path.isdir(p) and (target == p or target.startswith(p.rstrip("/") + "/")):
                QMessageBox.warning(self, "RetroVisor", "No se puede copiar o mover una carpeta dentro de sí misma.")
                return
        asker = dialogs.OverwriteAsker(self)
        pd = dialogs._progress(self, "Moviendo..." if move else "Copiando...", len(paths))
        errors = []
        last = None
        for i, p in enumerate(paths):
            pd.setValue(i)
            pd.setLabelText(os.path.basename(p))
            QApplication.processEvents()
            if pd.wasCanceled():
                break
            dest, err = fileops.copy_or_move(p, target, move, "ask", asker)
            if err == "cancel":
                break
            if err and err != "skip":
                errors.append(f"{os.path.basename(p)}: {err}")
            if dest:
                last = dest
        pd.setValue(len(paths))
        if errors:
            QMessageBox.warning(self, "RetroVisor", "\n".join(errors[:20]))
        if target == self.folder and last:
            self._load_folder(select=last)
        else:
            self.refresh(keep_selection=not move)

    def copy_move_to(self, move: bool):
        paths = self.selected_paths()
        if not paths:
            return
        recent = settings().get("general/recent_copy_dirs")
        d = dialogs.FolderPickDialog(move, recent[0] if recent else self.folder, self)
        if d.exec() == QDialog.Accepted:
            target = d.folder()
            if target and os.path.isdir(target):
                self.transfer(paths, target, move)
            elif target:
                if dialogs.ask_yes_no(self, "RetroVisor", f"La carpeta «{target}» no existe. ¿Desea crearla?"):
                    os.makedirs(target, exist_ok=True)
                    self.transfer(paths, target, move)

    def delete_selected(self, permanent: bool):
        self._delete_paths(self.selected_paths(), permanent)

    def _delete_paths(self, paths: list[str], permanent: bool):
        if not paths:
            return
        s = settings()
        trash = s.get("general/use_trash") and not permanent
        if s.get("general/confirm_delete") or permanent:
            what = f"«{os.path.basename(paths[0])}»" if len(paths) == 1 else f"estos {len(paths)} elementos"
            q = (f"¿Desea enviar {what} a la papelera?" if trash else
                 f"¿Desea eliminar definitivamente {what}?\nEsta operación no se puede deshacer.")
            if not dialogs.ask_yes_no(self, "Confirmar eliminación", q):
                return
        # elemento a seleccionar después
        rows = sorted(self.model.index_of[p] for p in paths if p in self.model.index_of)
        next_path = None
        if rows:
            after = rows[-1] + 1
            remaining = [e.path for i, e in enumerate(self.model.entries) if i not in rows]
            cand = [e.path for e in self.model.entries[after:] if e.path not in paths]
            next_path = cand[0] if cand else (remaining[-1] if remaining else None)
        errors = []
        for p in paths:
            ok, err = fileops.delete_path(p, trash)
            if not ok:
                errors.append(f"{os.path.basename(p)}: {err}")
            else:
                fileops.write_description(p, "") if os.path.dirname(p) == self.folder else None
        if errors:
            QMessageBox.warning(self, "Eliminar", "\n".join(errors[:20]))
        if self.folder in paths or not os.path.isdir(self.folder):
            self.go_up()
            return
        self._load_folder(select=next_path)

    def rename(self):
        view = self.current_view()
        idx = self.list_view.currentIndex()
        if not idx.isValid():
            return
        if len(self.selected_paths()) > 1:
            self.rename_series()
            return
        view.edit(self.model.index(idx.row(), 0))

    def rename_series(self):
        paths = [e.path for e in self.selected_entries() if not e.is_dir]
        if not paths:
            return
        d = dialogs.RenameSeriesDialog(paths, self)
        if d.exec() == QDialog.Accepted:
            mapping = d.run()
            self._load_folder(keep=list(mapping.values()))

    # ------------------------------------------------------------------ favoritos / recientes
    def add_favorite(self, path: str | None = None):
        path = path or self.folder
        favs = settings().get("general/favorites")
        if path not in favs:
            favs.append(path)
            settings().set("general/favorites", favs)
        self._fill_fav_list()
        self.statusBar().showMessage(f"Añadido a favoritos: {path}", 3000)

    def organize_favorites(self):
        if dialogs.FavoritesDialog(self).exec() == QDialog.Accepted:
            self._fill_fav_list()

    def _fill_fav_list(self):
        self.fav_list.clear()
        for f in settings().get("general/favorites"):
            it = QListWidgetItem(icon("folder"), os.path.basename(f.rstrip("/")) or f)
            it.setData(Qt.UserRole, f)
            it.setToolTip(f)
            self.fav_list.addItem(it)

    def _fill_fav_menu(self):
        m = self.fav_menu
        m.clear()
        m.addAction(self.a_fav_add)
        m.addAction(self.a_fav_org)
        favs = settings().get("general/favorites")
        if favs:
            m.addSeparator()
        for f in favs:
            m.addAction(icon("folder"), f, lambda p=f: self.navigate(p))

    def _fill_recent(self):
        self.recent_menu.clear()
        for f in settings().get("browser/recent_folders", []):
            if os.path.isdir(f):
                self.recent_menu.addAction(icon("folder"), f, lambda p=f: self.navigate(p))

    # ------------------------------------------------------------------ herramientas
    def filter_dialog(self):
        if dialogs.FilterDialog(self).exec() == QDialog.Accepted:
            self.reload_settings()

    def convert(self):
        paths = self.selected_images()
        if not paths:
            return
        d = dialogs.ConvertDialog(paths, self)
        if d.exec() == QDialog.Accepted:
            out = d.run()
            self._load_folder(keep=[p for p in out if os.path.dirname(p) == self.folder])

    def rotate(self):
        paths = self.selected_images()
        if not paths:
            return
        d = dialogs.RotateDialog(paths, self)
        if d.exec() == QDialog.Accepted:
            d.run()
            self._after_modify(paths)

    def quick_rotate(self, op: str):
        paths = self.selected_images()
        if not paths:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        errors = []
        try:
            for p in paths:
                try:
                    dialogs.rotate_file(p, op, True)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{os.path.basename(p)}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        if errors:
            QMessageBox.warning(self, "Rotar", "\n".join(errors[:20]))
        self._after_modify(paths)

    def _after_modify(self, paths):
        for p in paths:
            r = self.model.index_of.get(p)
            if r is not None:
                e = self.model.entries[r]
                e.thumb = None
                e.info = None
                e.thumb_failed = False
                self._requested.discard(p)
        self.preview.path = ""
        self.refresh(keep_selection=True)

    def resize_images(self):
        paths = self.selected_images()
        if not paths:
            return
        d = dialogs.BatchResizeDialog(paths, self)
        if d.exec() == QDialog.Accepted:
            out = d.run()
            self._after_modify(paths)
            self._load_folder(keep=[p for p in out if os.path.dirname(p) == self.folder])

    def moz_optimize(self):
        paths = [p for p in (self.selected_images() or self.image_list())
                 if Path(p).suffix.lower() in (".jpg", ".jpeg", ".jpe", ".jfif")]
        if not paths:
            QMessageBox.information(self, "MozJPEG", "No hay archivos JPEG seleccionados.")
            return
        if not dialogs.ask_yes_no(self, "Optimizar con MozJPEG",
                                  f"Se recomprimirán {len(paths)} archivos JPEG sin pérdida de calidad "
                                  "(la imagen queda idéntica, solo cambia la codificación).\n¿Continuar?"):
            return
        pd = dialogs._progress(self, "Optimizando con MozJPEG...", len(paths))
        before = after = 0
        for i, p in enumerate(paths):
            pd.setValue(i)
            pd.setLabelText(os.path.basename(p))
            QApplication.processEvents()
            if pd.wasCanceled():
                break
            try:
                b, a = imaging.mozjpeg_optimize_lossless(p)
                before += b
                after += a
            except Exception:
                pass
        pd.setValue(len(paths))
        saved = before - after
        pct = (saved / before * 100) if before else 0
        QMessageBox.information(self, "MozJPEG", f"Optimización terminada.\nAhorro: {human_size(saved)} ({pct:.1f} %)")
        self.refresh(keep_selection=True)

    def set_wallpaper(self, mode: str):
        paths = self.selected_images()
        if not paths:
            return
        ok, err = fileops.set_wallpaper(paths[0], mode)
        if ok:
            self.statusBar().showMessage("Fondo de escritorio cambiado", 3000)
        else:
            QMessageBox.warning(self, "Fondo de escritorio", f"No se pudo cambiar el fondo:\n{err}")

    def restore_wallpaper(self):
        ok, err = fileops.restore_wallpaper()
        if not ok:
            QMessageBox.information(self, "Fondo de escritorio", err)

    def edit_external(self):
        paths = self.selected_images()
        for p in paths[:10]:
            if not fileops.edit_external(p):
                QMessageBox.warning(self, "Editar", "No se encontró ningún editor de imágenes.\n"
                                                   "Puede configurarlo en Herramientas > Opciones.")
                return

    def open_default(self):
        for p in self.selected_paths()[:10]:
            fileops.open_with_default(p)

    def open_file_manager(self):
        sel = self.selected_paths()
        fileops.open_in_file_manager(sel[0] if sel else self.folder)

    def integrate(self):
        ok, msg = fileops.integrate_desktop()
        if ok:
            QMessageBox.information(self, "RetroVisor", "RetroVisor se añadió al menú de aplicaciones "
                                                        "(sección Gráficos).")
        else:
            QMessageBox.warning(self, "RetroVisor", msg)

    def make_default(self):
        ok, msg = fileops.set_default_viewer()
        if ok:
            QMessageBox.information(self, "RetroVisor", "RetroVisor es ahora el visor predeterminado: al hacer "
                                                        "doble clic en una imagen se abrirá con él.")
        else:
            QMessageBox.warning(self, "RetroVisor", f"No se pudo establecer como predeterminado:\n{msg}")

    def options(self):
        if dialogs.OptionsDialog(self).exec() == QDialog.Accepted:
            apply_theme()
            self.model.thumb_size = -1  # forzar regeneración si cambió algo de las miniaturas
            self.reload_settings()

    # ------------------------------------------------------------------ cierre
    def closeEvent(self, e):
        s = settings()
        s.set("browser/geometry", self.saveGeometry())
        s.set("browser/state", self.saveState())
        s.set("browser/splitter", self.splitter.saveState())
        s.set("browser/splitter_left", self.left_split.saveState())
        s.sync()
        self.loader.shutdown()
        super().closeEvent(e)
