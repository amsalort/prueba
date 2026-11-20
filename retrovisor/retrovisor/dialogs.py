"""Diálogos del programa."""
from __future__ import annotations

import datetime as _dt
import fnmatch
import os
import re
import stat
from pathlib import Path

from PIL import Image, ImageEnhance
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog,
                               QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit,
                               QProgressDialog, QPushButton, QRadioButton, QSlider, QSpinBox,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import imaging
from .fileops import read_descriptions, write_description
from .imaging import SAVE_FORMATS, mozjpeg_available, pil_to_qimage
from .settings import settings
from .util import human_size


def _buttons(dlg: QDialog, ok_text: str | None = None) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text or "Aceptar")
    bb.button(QDialogButtonBox.Cancel).setText("Cancelar")
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


def ask_yes_no(parent, title: str, text: str, cancel: bool = False):
    """Devuelve True/False (o None si se cancela)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Question)
    yes = box.addButton("&Sí", QMessageBox.YesRole)
    no = box.addButton("&No", QMessageBox.NoRole)
    if cancel:
        box.addButton("Cancelar", QMessageBox.RejectRole)
    box.setDefaultButton(yes)
    box.exec()
    c = box.clickedButton()
    if c is yes:
        return True
    if c is no:
        return False
    return None


def ask_overwrite(parent, dest: str) -> str | None:
    box = QMessageBox(parent)
    box.setWindowTitle("Confirmar reemplazo de archivo")
    box.setIcon(QMessageBox.Warning)
    box.setText(f"Ya existe un archivo llamado:\n{dest}\n\n¿Desea reemplazarlo?")
    b_yes = box.addButton("&Sí", QMessageBox.YesRole)
    b_all = box.addButton("Sí a &todo", QMessageBox.YesRole)
    b_ren = box.addButton("&Renombrar", QMessageBox.ActionRole)
    b_no = box.addButton("&No", QMessageBox.NoRole)
    b_none = box.addButton("No a t&odo", QMessageBox.NoRole)
    box.addButton("Cancelar", QMessageBox.RejectRole)
    box.exec()
    return {b_yes: "replace", b_all: "replace_all", b_ren: "rename", b_no: "skip",
            b_none: "skip_all"}.get(box.clickedButton())


class OverwriteAsker:
    """Gestiona 'Sí a todo' / 'No a todo' durante una operación por lotes."""

    def __init__(self, parent):
        self.parent = parent
        self.sticky: str | None = None

    def __call__(self, dest: str) -> str | None:
        if self.sticky:
            return self.sticky
        r = ask_overwrite(self.parent, dest)
        if r == "replace_all":
            self.sticky = r = "replace"
        elif r == "skip_all":
            self.sticky = r = "skip"
        return r


# =============================================================== opciones de formato

class FormatOptionsDialog(QDialog):
    """Opciones de compresión para JPEG, MozJPEG, PNG, WebP y TIFF."""

    def __init__(self, fmt: str, parent=None):
        super().__init__(parent)
        self.fmt = fmt.upper()
        s = settings()
        self.setWindowTitle("Opciones de " + SAVE_FORMATS.get(self.fmt, (self.fmt,))[0])
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)
        self.w: dict[str, QWidget] = {}
        if self.fmt in ("JPEG", "MOZJPEG"):
            pre = "mozjpeg" if self.fmt == "MOZJPEG" else "jpeg"
            if self.fmt == "MOZJPEG":
                note = QLabel("MozJPEG produce archivos JPEG compatibles con cualquier programa, "
                              "pero entre un 10 % y un 20 % más pequeños con la misma calidad visual.")
                note.setWordWrap(True)
                lay.insertWidget(0, note)
                if not mozjpeg_available():
                    warn = QLabel("<b>Aviso:</b> no se encontró el codificador cjpeg de MozJPEG; "
                                  "se usará la codificación estándar con optimización sin pérdida.")
                    warn.setWordWrap(True)
                    lay.insertWidget(1, warn)
            row = QHBoxLayout()
            sl = QSlider(Qt.Horizontal)
            sl.setRange(1, 100)
            sp = QSpinBox()
            sp.setRange(1, 100)
            sl.valueChanged.connect(sp.setValue)
            sp.valueChanged.connect(sl.setValue)
            sp.setValue(s.get(pre + "/quality"))
            lbl_lo, lbl_hi = QLabel("Mejor compresión"), QLabel("Mejor calidad")
            row.addWidget(lbl_lo)
            row.addWidget(sl, 1)
            row.addWidget(lbl_hi)
            row.addWidget(sp)
            form.addRow("Calidad:", row)
            self.w["quality"] = sp
            prog = QCheckBox("Codificación progresiva")
            prog.setChecked(s.get(pre + "/progressive"))
            form.addRow("", prog)
            self.w["progressive"] = prog
            sub = QComboBox()
            sub.addItems(["4:2:0", "4:2:2", "4:4:4"])
            sub.setCurrentText(s.get(pre + "/subsampling"))
            form.addRow("Submuestreo de color:", sub)
            self.w["subsampling"] = sub
            if self.fmt == "JPEG":
                opt = QCheckBox("Optimizar tablas Huffman")
                opt.setChecked(s.get("jpeg/optimize"))
                form.addRow("", opt)
                self.w["optimize"] = opt
            else:
                tr = QCheckBox("Cuantización Trellis (recomendado)")
                tr.setChecked(s.get("mozjpeg/trellis"))
                form.addRow("", tr)
                self.w["trellis"] = tr
                tune = QComboBox()
                for key, txt in (("psnr-hvs", "PSNR-HVS (predeterminado, visual)"), ("ssim", "SSIM"),
                                 ("ms-ssim", "MS-SSIM"), ("psnr", "PSNR")):
                    tune.addItem(txt, key)
                tune.setCurrentIndex(max(0, tune.findData(s.get("mozjpeg/tune"))))
                form.addRow("Ajuste de calidad:", tune)
                self.w["tune"] = tune
            ke = QCheckBox("Conservar datos EXIF")
            ke.setChecked(s.get(pre + "/keep_exif"))
            form.addRow("", ke)
            self.w["keep_exif"] = ke
        elif self.fmt == "PNG":
            sp = QSpinBox()
            sp.setRange(0, 9)
            sp.setValue(s.get("png/compress"))
            form.addRow("Nivel de compresión (0-9):", sp)
            self.w["compress"] = sp
        elif self.fmt == "WEBP":
            sp = QSpinBox()
            sp.setRange(1, 100)
            sp.setValue(s.get("webp/quality"))
            form.addRow("Calidad:", sp)
            self.w["quality"] = sp
            ll = QCheckBox("Sin pérdida")
            ll.setChecked(s.get("webp/lossless"))
            form.addRow("", ll)
            self.w["lossless"] = ll
        elif self.fmt == "TIFF":
            cb = QComboBox()
            for key, txt in (("none", "Ninguna"), ("tiff_lzw", "LZW"), ("tiff_deflate", "ZIP (Deflate)"),
                             ("packbits", "PackBits"), ("jpeg", "JPEG")):
                cb.addItem(txt, key)
            cb.setCurrentIndex(max(0, cb.findData(s.get("tiff/compression"))))
            form.addRow("Compresión:", cb)
            self.w["compression"] = cb
        else:
            form.addRow(QLabel("Este formato no tiene opciones."))
        self.save_default = QCheckBox("Guardar como valores predeterminados")
        self.save_default.setChecked(True)
        lay.addWidget(self.save_default)
        lay.addWidget(_buttons(self))

    @staticmethod
    def has_options(fmt: str) -> bool:
        return fmt.upper() in ("JPEG", "MOZJPEG", "PNG", "WEBP", "TIFF")

    def options(self) -> dict:
        out = {}
        for k, w in self.w.items():
            if isinstance(w, QSpinBox):
                out[k] = w.value()
            elif isinstance(w, QCheckBox):
                out[k] = w.isChecked()
            elif isinstance(w, QComboBox):
                out[k] = w.currentData() if w.currentData() is not None else w.currentText()
        return out

    def accept(self):
        if self.save_default.isChecked():
            pre = {"MOZJPEG": "mozjpeg", "JPEG": "jpeg", "PNG": "png", "WEBP": "webp", "TIFF": "tiff"}.get(self.fmt)
            if pre:
                for k, v in self.options().items():
                    settings().set(f"{pre}/{k}", v)
        super().accept()


def ask_save_path(parent, start_path: str, default_fmt: str | None = None) -> tuple[str, str, dict] | None:
    """Diálogo 'Guardar como'. Devuelve (ruta, clave_formato, opciones) o None."""
    filters = []
    keys = list(SAVE_FORMATS)
    for k in keys:
        name, ext, _ = SAVE_FORMATS[k]
        exts = {"JPEG": "*.jpg *.jpeg *.jpe *.jfif", "MOZJPEG": "*.jpg *.jpeg", "TIFF": "*.tif *.tiff",
                "PPM": "*.ppm *.pgm *.pbm"}.get(k, "*" + ext)
        filters.append(f"{name} ({exts})")
    ext = Path(start_path).suffix.lower()
    fmt = default_fmt or imaging.EXT_TO_SAVE.get(ext, "JPEG")
    sel = filters[keys.index(fmt)] if fmt in keys else filters[1]
    path, chosen = QFileDialog.getSaveFileName(parent, "Guardar como", start_path, ";;".join(filters), sel)
    if not path:
        return None
    fmt = keys[filters.index(chosen)] if chosen in filters else imaging.EXT_TO_SAVE.get(Path(path).suffix.lower(), "JPEG")
    want_ext = SAVE_FORMATS[fmt][1]
    valid = {k for k, v in imaging.EXT_TO_SAVE.items() if v == ("JPEG" if fmt == "MOZJPEG" else fmt)}
    if Path(path).suffix.lower() not in valid:
        path += want_ext
    opts: dict = {}
    if FormatOptionsDialog.has_options(fmt):
        d = FormatOptionsDialog(fmt, parent)
        if d.exec() != QDialog.Accepted:
            return None
        opts = d.options()
    return path, fmt, opts


# =============================================================== propiedades

class PropertiesDialog(QDialog):
    def __init__(self, path: str, loaded=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle("Propiedades de " + os.path.basename(path))
        self.resize(520, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # -- Archivo
        w = QWidget()
        f = QFormLayout(w)
        st = os.stat(path)
        def sel(text):
            lab = QLabel(text)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setWordWrap(True)
            return lab
        f.addRow("Nombre:", sel(os.path.basename(path)))
        f.addRow("Ubicación:", sel(os.path.dirname(path)))
        f.addRow("Tamaño:", sel(f"{human_size(st.st_size)} ({st.st_size:,} bytes)".replace(",", ".")))
        fmt_dt = lambda t: _dt.datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M:%S")
        f.addRow("Modificado:", sel(fmt_dt(st.st_mtime)))
        f.addRow("Último acceso:", sel(fmt_dt(st.st_atime)))
        f.addRow("Cambio de estado:", sel(fmt_dt(st.st_ctime)))
        f.addRow("Permisos:", sel(stat.filemode(st.st_mode)))
        ro = QCheckBox("Sólo lectura")
        ro.setChecked(not (st.st_mode & stat.S_IWUSR))
        self.ro = ro
        hidden = QCheckBox("Oculto")
        hidden.setChecked(os.path.basename(path).startswith("."))
        hidden.setEnabled(False)
        attrs = QHBoxLayout()
        attrs.addWidget(ro)
        attrs.addWidget(hidden)
        attrs.addStretch()
        f.addRow("Atributos:", attrs)
        tabs.addTab(w, "Archivo")

        # -- Descripción (descript.ion, compatible con los visores de Windows)
        dw = QWidget()
        dl = QVBoxLayout(dw)
        dl.addWidget(QLabel("Descripción (se guarda en el archivo descript.ion de la carpeta):"))
        self.desc = QPlainTextEdit()
        self.desc.setPlainText(read_descriptions(os.path.dirname(path)).get(os.path.basename(path).lower(), ""))
        dl.addWidget(self.desc)
        tabs.addTab(dw, "Descripción")

        # -- Imagen
        if imaging.is_image(path):
            iw = QWidget()
            fi = QFormLayout(iw)
            info = imaging.read_info(path)
            fi.addRow("Formato:", sel(imaging.FORMAT_NAMES.get(info.format, info.format or "?")))
            fi.addRow("Dimensiones:", sel(f"{info.width} x {info.height} píxeles"))
            fi.addRow("Profundidad de color:", sel(f"{info.bpp} bits por píxel ({info.mode})"))
            if info.width and info.height:
                mp = info.width * info.height / 1e6
                fi.addRow("Megapíxeles:", sel(f"{mp:.2f}"))
            if info.frames > 1:
                fi.addRow("Cuadros:", sel(str(info.frames)))
            try:
                with Image.open(path) as im:
                    dpi = im.info.get("dpi")
                    if dpi:
                        fi.addRow("Resolución:", sel(f"{round(float(dpi[0]))} x {round(float(dpi[1]))} ppp"))
                    if im.info.get("progressive") or im.info.get("progression"):
                        fi.addRow("Codificación:", sel("Progresiva"))
                    if im.format == "JPEG":
                        q = _estimate_jpeg_quality(im)
                        if q:
                            fi.addRow("Calidad JPEG estimada:", sel(f"≈ {q}"))
                    if "icc_profile" in im.info:
                        fi.addRow("Perfil de color:", sel("ICC incrustado"))
                    comment = im.info.get("comment")
                    if comment:
                        if isinstance(comment, bytes):
                            comment = comment.decode(errors="replace")
                        fi.addRow("Comentario:", sel(str(comment)[:500]))
            except Exception:
                pass
            if loaded is not None:
                fi.addRow("Tiempo de carga:", sel(f"{loaded.load_ms} ms"))
            tabs.addTab(iw, "Imagen")
            rows = imaging.exif_table(path)
            if rows:
                t = QTableWidget(len(rows), 2)
                t.setHorizontalHeaderLabels(["Etiqueta", "Valor"])
                t.verticalHeader().hide()
                t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
                t.setEditTriggers(QAbstractItemView.NoEditTriggers)
                for i, (k, v) in enumerate(rows):
                    t.setItem(i, 0, QTableWidgetItem(k))
                    t.setItem(i, 1, QTableWidgetItem(v))
                t.resizeColumnToContents(0)
                tabs.addTab(t, "EXIF")
        lay.addWidget(_buttons(self))

    def accept(self):
        try:
            write_description(self.path, self.desc.toPlainText().replace("\n", " "))
        except OSError as e:
            QMessageBox.warning(self, "Descripción", f"No se pudo guardar la descripción:\n{e}")
        try:
            mode = os.stat(self.path).st_mode
            if self.ro.isChecked():
                os.chmod(self.path, mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            elif not mode & stat.S_IWUSR:
                os.chmod(self.path, mode | stat.S_IWUSR)
        except OSError:
            pass
        super().accept()


def _estimate_jpeg_quality(im) -> int | None:
    try:
        q = im.quantization
        if not q:
            return None
        t = q[0]
        std = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
               14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
               49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99]
        ratio = sum(t) / sum(std) * 100
        qual = (200 - ratio) / 2 if ratio <= 100 else 5000 / ratio
        return max(1, min(100, round(qual)))
    except Exception:
        return None


# =============================================================== redimensionar

FILTERS = [("Lanczos (mejor calidad)", Image.Resampling.LANCZOS), ("Bicúbico", Image.Resampling.BICUBIC),
           ("Bilineal", Image.Resampling.BILINEAR), ("Vecino más cercano", Image.Resampling.NEAREST)]


class ResizeDialog(QDialog):
    def __init__(self, width: int | None, height: int | None, parent=None, batch: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Redimensionar")
        self.w0, self.h0 = width or 0, height or 0
        lay = QVBoxLayout(self)
        g = QGroupBox("Tamaño")
        gl = QGridLayout(g)
        self.r_pct = QRadioButton("Porcentaje del original:")
        self.r_px = QRadioButton("Tamaño en píxeles:")
        self.r_fit = QRadioButton("Ajustar dentro de (lado mayor):")
        self.pct = QSpinBox()
        self.pct.setRange(1, 1000)
        self.pct.setValue(50)
        self.pct.setSuffix(" %")
        self.wid = QSpinBox()
        self.hei = QSpinBox()
        for sp in (self.wid, self.hei):
            sp.setRange(1, 65535)
        self.wid.setValue(self.w0 or 800)
        self.hei.setValue(self.h0 or 600)
        self.longside = QSpinBox()
        self.longside.setRange(1, 65535)
        self.longside.setValue(1024)
        self.keep = QCheckBox("Mantener proporciones")
        self.keep.setChecked(True)
        gl.addWidget(self.r_pct, 0, 0)
        gl.addWidget(self.pct, 0, 1)
        gl.addWidget(self.r_px, 1, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Ancho"))
        row.addWidget(self.wid)
        row.addWidget(QLabel("Alto"))
        row.addWidget(self.hei)
        gl.addLayout(row, 1, 1)
        gl.addWidget(self.keep, 2, 1)
        gl.addWidget(self.r_fit, 3, 0)
        gl.addWidget(self.longside, 3, 1)
        if batch:
            self.r_fit.setChecked(True)
        else:
            self.r_px.setChecked(True)
            gl.addWidget(QLabel(f"Tamaño actual: {self.w0} x {self.h0}"), 4, 0, 1, 2)
        lay.addWidget(g)
        f = QFormLayout()
        self.filter = QComboBox()
        for name, _ in FILTERS:
            self.filter.addItem(name)
        f.addRow("Filtro de remuestreo:", self.filter)
        lay.addLayout(f)
        self._lock = False
        self.wid.valueChanged.connect(self._w_changed)
        self.hei.valueChanged.connect(self._h_changed)
        lay.addWidget(_buttons(self))

    def _w_changed(self, v):
        if self._lock or not self.keep.isChecked() or not self.w0:
            return
        self._lock = True
        self.hei.setValue(max(1, round(v * self.h0 / self.w0)))
        self._lock = False

    def _h_changed(self, v):
        if self._lock or not self.keep.isChecked() or not self.h0:
            return
        self._lock = True
        self.wid.setValue(max(1, round(v * self.w0 / self.h0)))
        self._lock = False

    def target_size(self, w: int, h: int) -> tuple[int, int]:
        if self.r_pct.isChecked():
            p = self.pct.value() / 100
            return max(1, round(w * p)), max(1, round(h * p))
        if self.r_fit.isChecked():
            m = self.longside.value()
            if max(w, h) <= m:
                return w, h
            sc = m / max(w, h)
            return max(1, round(w * sc)), max(1, round(h * sc))
        tw, th = self.wid.value(), self.hei.value()
        if self.keep.isChecked() and (w, h) != (self.w0, self.h0):
            sc = min(tw / w, th / h)
            return max(1, round(w * sc)), max(1, round(h * sc))
        return tw, th

    def resample(self):
        return FILTERS[self.filter.currentIndex()][1]


# =============================================================== ajustes de color

class AdjustDialog(QDialog):
    """Brillo, contraste, gamma, saturación y nitidez con vista previa."""

    def __init__(self, pil: Image.Image, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajustar imagen")
        self.src = pil
        prev = pil.copy()
        prev.thumbnail((420, 320))
        self.prev_src = prev
        lay = QVBoxLayout(self)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(420, 320)
        self.preview.setFrameShape(QFrame.StyledPanel)
        lay.addWidget(self.preview)
        g = QGridLayout()
        self.sliders: dict[str, QSlider] = {}
        for i, (key, label) in enumerate((("brightness", "Brillo"), ("contrast", "Contraste"),
                                          ("gamma", "Gamma"), ("saturation", "Saturación"),
                                          ("sharpness", "Nitidez"))):
            sl = QSlider(Qt.Horizontal)
            sl.setRange(-100, 100)
            sl.setValue(0)
            val = QLabel("0")
            val.setMinimumWidth(32)
            sl.valueChanged.connect(lambda v, lab=val: lab.setText(str(v)))
            sl.valueChanged.connect(self._schedule)
            g.addWidget(QLabel(label + ":"), i, 0)
            g.addWidget(sl, i, 1)
            g.addWidget(val, i, 2)
            self.sliders[key] = sl
        lay.addLayout(g)
        bb = _buttons(self)
        reset = bb.addButton("Restablecer", QDialogButtonBox.ResetRole)
        reset.clicked.connect(lambda: [s.setValue(0) for s in self.sliders.values()])
        lay.addWidget(bb)
        self._timer = QTimer(self, singleShot=True, interval=60)
        self._timer.timeout.connect(self._update_preview)
        self._update_preview()

    def _schedule(self):
        self._timer.start()

    def apply(self, im: Image.Image) -> Image.Image:
        v = {k: s.value() for k, s in self.sliders.items()}
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        out = im.convert("RGB") if im.mode not in ("RGB", "L") else im
        if v["brightness"]:
            out = ImageEnhance.Brightness(out).enhance(1 + v["brightness"] / 100)
        if v["contrast"]:
            out = ImageEnhance.Contrast(out).enhance(1 + v["contrast"] / 100)
        if v["gamma"]:
            gamma = 2 ** (-v["gamma"] / 50)
            lut = [min(255, round(255 * ((i / 255) ** gamma))) for i in range(256)]
            out = out.point(lut * (len(out.getbands())))
        if v["saturation"] and out.mode == "RGB":
            out = ImageEnhance.Color(out).enhance(1 + v["saturation"] / 100)
        if v["sharpness"]:
            out = ImageEnhance.Sharpness(out).enhance(1 + v["sharpness"] / 50)
        if alpha is not None:
            out = out.convert("RGBA")
            out.putalpha(alpha)
        return out

    def _update_preview(self):
        qi = pil_to_qimage(self.apply(self.prev_src))
        self.preview.setPixmap(QPixmap.fromImage(qi))


# =============================================================== opciones

class OptionsDialog(QDialog):
    def __init__(self, parent=None, page: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Opciones")
        self.resize(520, 460)
        s = settings()
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # General
        w = QWidget()
        f = QFormLayout(w)
        self.confirm_delete = QCheckBox("Confirmar antes de eliminar")
        self.confirm_delete.setChecked(s.get("general/confirm_delete"))
        self.use_trash = QCheckBox("Enviar archivos eliminados a la papelera")
        self.use_trash.setChecked(s.get("general/use_trash"))
        self.autorot = QCheckBox("Girar automáticamente según la orientación EXIF")
        self.autorot.setChecked(s.get("general/exif_autorotate"))
        f.addRow(self.confirm_delete)
        f.addRow(self.use_trash)
        f.addRow(self.autorot)
        row = QHBoxLayout()
        self.startup = QLineEdit(s.get("general/startup_folder"))
        self.startup.setPlaceholderText("(última carpeta visitada)")
        b = QPushButton("...")
        b.clicked.connect(lambda: self._pick_dir(self.startup))
        row.addWidget(self.startup)
        row.addWidget(b)
        f.addRow("Carpeta de inicio:", row)
        self.editor = QLineEdit(s.get("general/external_editor"))
        self.editor.setPlaceholderText("(automático: GIMP, Pinta, Krita...)")
        f.addRow("Editor externo:", self.editor)
        tabs.addTab(w, "General")

        # Apariencia
        w = QWidget()
        f = QFormLayout(w)
        self.theme = QComboBox()
        for key, txt in (("light", "Claro"), ("dark", "Oscuro"), ("system", "Según el sistema")):
            self.theme.addItem(txt, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.get("appearance/theme"))))
        self.style = QComboBox()
        self.style.addItem("Clásico (Windows 98)", "Windows")
        self.style.addItem("Moderno (Fusion)", "Fusion")
        self.style.setCurrentIndex(max(0, self.style.findData(s.get("appearance/style"))))
        f.addRow("Modo de color:", self.theme)
        f.addRow("Estilo de ventanas:", self.style)
        tabs.addTab(w, "Apariencia")

        # Explorador
        w = QWidget()
        f = QFormLayout(w)
        self.thumb_size = QSpinBox()
        self.thumb_size.setRange(32, 512)
        self.thumb_size.setSingleStep(8)
        self.thumb_size.setValue(s.get("browser/thumb_size"))
        self.thumb_size.setSuffix(" px")
        f.addRow("Tamaño de las miniaturas:", self.thumb_size)
        self.thumb_name = QCheckBox("Mostrar el nombre bajo la miniatura")
        self.thumb_name.setChecked(s.get("browser/thumb_show_name"))
        self.thumb_info = QCheckBox("Mostrar dimensiones bajo la miniatura")
        self.thumb_info.setChecked(s.get("browser/thumb_show_info"))
        self.thumb_cache = QCheckBox("Guardar miniaturas en caché (más rápido)")
        self.thumb_cache.setChecked(s.get("browser/thumb_cache"))
        self.show_hidden = QCheckBox("Mostrar archivos ocultos")
        self.show_hidden.setChecked(s.get("browser/show_hidden"))
        self.show_folders = QCheckBox("Mostrar carpetas en la lista de archivos")
        self.show_folders.setChecked(s.get("browser/show_folders"))
        for cb in (self.thumb_name, self.thumb_info, self.thumb_cache, self.show_hidden, self.show_folders):
            f.addRow(cb)
        from .thumbs import cache_size, clear_cache

        row = QHBoxLayout()
        lab = QLabel(f"Caché: {human_size(cache_size())}")
        btn = QPushButton("Vaciar caché")
        btn.clicked.connect(lambda: (clear_cache(), lab.setText("Caché: 0 bytes")))
        row.addWidget(lab)
        row.addWidget(btn)
        row.addStretch()
        f.addRow(row)
        tabs.addTab(w, "Explorador")

        # Visor
        w = QWidget()
        f = QFormLayout(w)
        self.fit = QComboBox()
        for key, txt in (("shrink", "Reducir las imágenes grandes para ajustar"),
                         ("fit", "Ajustar todas las imágenes a la ventana"),
                         ("none", "Tamaño real (100 %)")):
            self.fit.addItem(txt, key)
        self.fit.setCurrentIndex(max(0, self.fit.findData(s.get("viewer/fit"))))
        f.addRow("Tamaño de imagen:", self.fit)
        self.smooth = QCheckBox("Suavizar al ampliar o reducir")
        self.smooth.setChecked(s.get("viewer/smooth"))
        self.fullscreen = QCheckBox("Abrir el visor a pantalla completa")
        self.fullscreen.setChecked(s.get("viewer/fullscreen"))
        self.wrap = QCheckBox("Al llegar al final, volver a la primera imagen")
        self.wrap.setChecked(s.get("viewer/wrap"))
        self.preload = QCheckBox("Precargar la imagen siguiente")
        self.preload.setChecked(s.get("viewer/preload"))
        self.fs_info = QCheckBox("Mostrar información en pantalla completa")
        self.fs_info.setChecked(s.get("viewer/fs_show_info"))
        for cb in (self.smooth, self.fullscreen, self.wrap, self.preload, self.fs_info):
            f.addRow(cb)
        self.wheel = QComboBox()
        self.wheel.addItem("Imagen anterior / siguiente", "navigate")
        self.wheel.addItem("Acercar / alejar", "zoom")
        self.wheel.setCurrentIndex(max(0, self.wheel.findData(s.get("viewer/wheel"))))
        f.addRow("Rueda del ratón:", self.wheel)
        self.bg = s.get("viewer/bgcolor")
        self.bg_btn = QPushButton()
        self._paint_bg_btn()
        self.bg_btn.clicked.connect(self._pick_bg)
        f.addRow("Color de fondo:", self.bg_btn)
        tabs.addTab(w, "Visor")

        # Presentación
        w = QWidget()
        f = QFormLayout(w)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.2, 3600)
        self.delay.setDecimals(1)
        self.delay.setSuffix(" s")
        self.delay.setValue(s.get("slideshow/delay"))
        f.addRow("Retardo entre imágenes:", self.delay)
        self.loop = QCheckBox("Repetir continuamente")
        self.loop.setChecked(s.get("slideshow/loop"))
        self.rnd = QCheckBox("Orden aleatorio")
        self.rnd.setChecked(s.get("slideshow/random"))
        self.rev = QCheckBox("Orden inverso")
        self.rev.setChecked(s.get("slideshow/reverse"))
        self.ss_fs = QCheckBox("Pantalla completa")
        self.ss_fs.setChecked(s.get("slideshow/fullscreen"))
        for cb in (self.loop, self.rnd, self.rev, self.ss_fs):
            f.addRow(cb)
        tabs.addTab(w, "Presentación")

        # JPEG / MozJPEG
        w = QWidget()
        v = QVBoxLayout(w)
        b1 = QPushButton("Opciones de JPEG estándar...")
        b1.clicked.connect(lambda: FormatOptionsDialog("JPEG", self).exec())
        b2 = QPushButton("Opciones de MozJPEG...")
        b2.clicked.connect(lambda: FormatOptionsDialog("MOZJPEG", self).exec())
        st = QLabel("Codificador MozJPEG: " + ("disponible ✔" if mozjpeg_available() else
                                                "no encontrado (se usará el respaldo)"))
        v.addWidget(b1)
        v.addWidget(b2)
        v.addWidget(st)
        v.addStretch()
        tabs.addTab(w, "JPEG / MozJPEG")
        tabs.setCurrentIndex(page)
        lay.addWidget(_buttons(self))

    def _pick_dir(self, edit: QLineEdit):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)

    def _paint_bg_btn(self):
        pm = QPixmap(48, 16)
        pm.fill(QColor(self.bg))
        self.bg_btn.setIcon(pm)
        self.bg_btn.setIconSize(QSize(48, 16))
        self.bg_btn.setText(self.bg)

    def _pick_bg(self):
        c = QColorDialog.getColor(QColor(self.bg), self, "Color de fondo del visor")
        if c.isValid():
            self.bg = c.name()
            self._paint_bg_btn()

    def accept(self):
        s = settings()
        s.set("general/confirm_delete", self.confirm_delete.isChecked())
        s.set("general/use_trash", self.use_trash.isChecked())
        s.set("general/exif_autorotate", self.autorot.isChecked())
        s.set("general/startup_folder", self.startup.text().strip())
        s.set("general/external_editor", self.editor.text().strip())
        s.set("appearance/theme", self.theme.currentData())
        s.set("appearance/style", self.style.currentData())
        s.set("browser/thumb_size", self.thumb_size.value())
        s.set("browser/thumb_show_name", self.thumb_name.isChecked())
        s.set("browser/thumb_show_info", self.thumb_info.isChecked())
        s.set("browser/thumb_cache", self.thumb_cache.isChecked())
        s.set("browser/show_hidden", self.show_hidden.isChecked())
        s.set("browser/show_folders", self.show_folders.isChecked())
        s.set("viewer/fit", self.fit.currentData())
        s.set("viewer/smooth", self.smooth.isChecked())
        s.set("viewer/fullscreen", self.fullscreen.isChecked())
        s.set("viewer/wrap", self.wrap.isChecked())
        s.set("viewer/preload", self.preload.isChecked())
        s.set("viewer/fs_show_info", self.fs_info.isChecked())
        s.set("viewer/wheel", self.wheel.currentData())
        s.set("viewer/bgcolor", self.bg)
        s.set("slideshow/delay", self.delay.value())
        s.set("slideshow/loop", self.loop.isChecked())
        s.set("slideshow/random", self.rnd.isChecked())
        s.set("slideshow/reverse", self.rev.isChecked())
        s.set("slideshow/fullscreen", self.ss_fs.isChecked())
        s.sync()
        super().accept()


# =============================================================== lotes

def _progress(parent, title: str, n: int) -> QProgressDialog:
    pd = QProgressDialog(title, "Cancelar", 0, n, parent)
    pd.setWindowTitle(title)
    pd.setWindowModality(Qt.WindowModal)
    pd.setMinimumDuration(300)
    return pd


class ConvertDialog(QDialog):
    """Convertir varios archivos a otro formato (incluido MozJPEG)."""

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle("Convertir formato de imagen")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Archivos seleccionados: {len(paths)}"))
        g = QGroupBox("Formato de salida")
        gl = QVBoxLayout(g)
        self.fmt = QListWidget()
        for k, (name, ext, _) in SAVE_FORMATS.items():
            self.fmt.addItem(f"{name}  ({ext})")
        self.fmt.setCurrentRow(0)
        gl.addWidget(self.fmt)
        self.opts_btn = QPushButton("Opciones de formato...")
        self.opts_btn.clicked.connect(self._options)
        gl.addWidget(self.opts_btn, 0, Qt.AlignLeft)
        lay.addWidget(g)
        g2 = QGroupBox("Destino")
        f = QFormLayout(g2)
        self.same = QRadioButton("Misma carpeta que el original")
        self.other = QRadioButton("Otra carpeta:")
        self.same.setChecked(True)
        row = QHBoxLayout()
        self.dest = QLineEdit(os.path.dirname(paths[0]) if paths else "")
        b = QPushButton("...")
        b.clicked.connect(self._pick)
        row.addWidget(self.dest)
        row.addWidget(b)
        f.addRow(self.same)
        f.addRow(self.other, row)
        self.overwrite = QComboBox()
        self.overwrite.addItem("Preguntar", "ask")
        self.overwrite.addItem("Reemplazar", "replace")
        self.overwrite.addItem("Omitir", "skip")
        self.overwrite.addItem("Renombrar", "rename")
        f.addRow("Si el archivo existe:", self.overwrite)
        self.delete_orig = QCheckBox("Eliminar los originales tras convertir (a la papelera)")
        f.addRow(self.delete_orig)
        lay.addWidget(g2)
        lay.addWidget(_buttons(self, "Convertir"))
        self.opts: dict = {}

    def fmt_key(self) -> str:
        return list(SAVE_FORMATS)[self.fmt.currentRow()]

    def _options(self):
        d = FormatOptionsDialog(self.fmt_key(), self)
        if d.exec() == QDialog.Accepted:
            self.opts = d.options()

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de destino", self.dest.text())
        if d:
            self.dest.setText(d)
            self.other.setChecked(True)

    def run(self) -> list[str]:
        from .fileops import delete_path
        from .util import unique_path

        key = self.fmt_key()
        ext = SAVE_FORMATS[key][1]
        out_paths: list[str] = []
        errors: list[str] = []
        asker = OverwriteAsker(self.parentWidget())
        pd = _progress(self.parentWidget(), "Convirtiendo...", len(self.paths))
        autorot = settings().get("general/exif_autorotate")
        for i, p in enumerate(self.paths):
            pd.setValue(i)
            pd.setLabelText(f"Convirtiendo {os.path.basename(p)}...")
            QApplication.processEvents()
            if pd.wasCanceled():
                break
            folder = os.path.dirname(p) if self.same.isChecked() else self.dest.text()
            dest = Path(folder) / (Path(p).stem + ext)
            if dest.exists() and dest.resolve() != Path(p).resolve():
                mode = self.overwrite.currentData()
                if mode == "ask":
                    mode = asker(str(dest))
                if mode is None:
                    break
                if mode == "skip":
                    continue
                if mode == "rename":
                    dest = unique_path(dest)
            try:
                li = imaging.load_image(p, autorotate=autorot)
                imaging.save_image(li.pil, str(dest), key, self.opts, exif=li.exif, icc=li.icc)
                out_paths.append(str(dest))
                if self.delete_orig.isChecked() and Path(dest).resolve() != Path(p).resolve():
                    delete_path(p, True)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(p)}: {e}")
        pd.setValue(len(self.paths))
        if errors:
            QMessageBox.warning(self.parentWidget(), "Convertir", "Errores:\n" + "\n".join(errors[:20]))
        return out_paths


class RotateDialog(QDialog):
    """Rotar/voltear varios archivos (JPEG sin pérdida con jpegtran)."""

    OPS = [("90", "Rotar 90° a la derecha"), ("270", "Rotar 90° a la izquierda"), ("180", "Rotar 180°"),
           ("horizontal", "Voltear horizontalmente"), ("vertical", "Voltear verticalmente")]

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle("Rotar / Voltear")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Archivos seleccionados: {len(paths)}"))
        self.group = QButtonGroup(self)
        for i, (_k, txt) in enumerate(self.OPS):
            rb = QRadioButton(txt)
            self.group.addButton(rb, i)
            lay.addWidget(rb)
        self.group.button(0).setChecked(True)
        self.lossless = QCheckBox("Rotación JPEG sin pérdida (recomendado)")
        self.lossless.setChecked(True)
        lay.addWidget(self.lossless)
        lay.addWidget(_buttons(self, "Aplicar"))

    def run(self) -> None:
        op = self.OPS[self.group.checkedId()][0]
        errors = []
        pd = _progress(self.parentWidget(), "Rotando...", len(self.paths))
        for i, p in enumerate(self.paths):
            pd.setValue(i)
            QApplication.processEvents()
            if pd.wasCanceled():
                break
            try:
                rotate_file(p, op, self.lossless.isChecked())
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(p)}: {e}")
        pd.setValue(len(self.paths))
        if errors:
            QMessageBox.warning(self.parentWidget(), "Rotar", "Errores:\n" + "\n".join(errors[:20]))


def rotate_file(path: str, op: str, lossless: bool = True) -> None:
    ext = Path(path).suffix.lower()
    if lossless and ext in (".jpg", ".jpeg", ".jpe", ".jfif"):
        if imaging.jpeg_lossless_transform(path, op):
            return
    li = imaging.load_image(path)
    tr = {"90": Image.Transpose.ROTATE_270, "270": Image.Transpose.ROTATE_90, "180": Image.Transpose.ROTATE_180,
          "horizontal": Image.Transpose.FLIP_LEFT_RIGHT, "vertical": Image.Transpose.FLIP_TOP_BOTTOM}[op]
    out = li.pil.transpose(tr)
    fmt = imaging.EXT_TO_SAVE.get(ext, "PNG")
    if fmt == "JPEG":
        settings_q = {"quality": 95}
    else:
        settings_q = {}
    imaging.save_image(out, path, fmt, settings_q, exif=li.exif, icc=li.icc)


class BatchResizeDialog(ResizeDialog):
    def __init__(self, paths: list[str], parent=None):
        w = h = None
        if len(paths) == 1:
            info = imaging.read_info(paths[0])
            w, h = info.width, info.height
        super().__init__(w, h, parent, batch=len(paths) > 1)
        self.paths = paths
        lay = self.layout()
        g = QGroupBox("Salida")
        f = QFormLayout(g)
        self.overwrite_orig = QRadioButton("Sobrescribir los originales")
        self.new_names = QRadioButton("Crear archivos nuevos con sufijo:")
        self.suffix = QLineEdit("_redim")
        self.new_names.setChecked(True)
        f.addRow(self.overwrite_orig)
        f.addRow(self.new_names, self.suffix)
        self.out_fmt = QComboBox()
        self.out_fmt.addItem("Mismo formato que el original", None)
        for k, (name, _e, _f) in SAVE_FORMATS.items():
            self.out_fmt.addItem(name, k)
        f.addRow("Formato:", self.out_fmt)
        lay.insertWidget(lay.count() - 1, g)

    def run(self) -> list[str]:
        from .util import unique_path

        out = []
        errors = []
        pd = _progress(self.parentWidget(), "Redimensionando...", len(self.paths))
        for i, p in enumerate(self.paths):
            pd.setValue(i)
            QApplication.processEvents()
            if pd.wasCanceled():
                break
            try:
                li = imaging.load_image(p, autorotate=settings().get("general/exif_autorotate"))
                tw, th = self.target_size(li.width, li.height)
                im = li.pil.resize((tw, th), self.resample())
                key = self.out_fmt.currentData() or imaging.EXT_TO_SAVE.get(Path(p).suffix.lower(), "PNG")
                ext = SAVE_FORMATS[key][1]
                if self.overwrite_orig.isChecked():
                    dest = Path(p) if key == imaging.EXT_TO_SAVE.get(Path(p).suffix.lower()) else Path(p).with_suffix(ext)
                else:
                    dest = unique_path(Path(p).with_name(Path(p).stem + self.suffix.text() +
                                                        (Path(p).suffix if not self.out_fmt.currentData() else ext)))
                imaging.save_image(im, str(dest), key, {}, exif=li.exif, icc=li.icc)
                out.append(str(dest))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{os.path.basename(p)}: {e}")
        pd.setValue(len(self.paths))
        if errors:
            QMessageBox.warning(self.parentWidget(), "Redimensionar", "Errores:\n" + "\n".join(errors[:20]))
        return out


class RenameSeriesDialog(QDialog):
    """Renombrar en serie con plantilla: los '#' se sustituyen por un número correlativo."""

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.paths = paths
        self.setWindowTitle("Renombrar en serie")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        f = QFormLayout()
        self.template = QLineEdit("Imagen###")
        f.addRow("Plantilla:", self.template)
        hint = QLabel("Use # para los dígitos del número (### → 001, 002...). "
                      "Use * para el nombre original. Use ? para la fecha (AAAAMMDD).")
        hint.setWordWrap(True)
        f.addRow("", hint)
        self.start = QSpinBox()
        self.start.setRange(0, 9_999_999)
        self.start.setValue(1)
        f.addRow("Empezar en:", self.start)
        self.ext_case = QComboBox()
        self.ext_case.addItems(["Sin cambios", "minúsculas", "MAYÚSCULAS"])
        f.addRow("Extensión:", self.ext_case)
        lay.addLayout(f)
        self.preview = QTableWidget(0, 2)
        self.preview.setHorizontalHeaderLabels(["Nombre actual", "Nombre nuevo"])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.preview.verticalHeader().hide()
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.preview)
        lay.addWidget(_buttons(self, "Renombrar"))
        for w in (self.template.textChanged, self.start.valueChanged, self.ext_case.currentIndexChanged):
            w.connect(self._update)
        self._update()

    def new_names(self) -> list[str]:
        out = []
        tpl = self.template.text() or "#"
        for i, p in enumerate(self.paths):
            n = self.start.value() + i
            stem, ext = os.path.splitext(os.path.basename(p))
            name = re.sub(r"#+", lambda m: str(n).zfill(len(m.group(0))), tpl)
            if "#" not in tpl and "*" not in tpl:
                name += str(n)
            name = name.replace("*", stem)
            if "?" in name:
                d = _dt.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y%m%d")
                name = name.replace("?", d)
            if self.ext_case.currentIndex() == 1:
                ext = ext.lower()
            elif self.ext_case.currentIndex() == 2:
                ext = ext.upper()
            out.append(name + ext)
        return out

    def _update(self):
        names = self.new_names()
        self.preview.setRowCount(len(self.paths))
        for i, (p, n) in enumerate(zip(self.paths, names)):
            self.preview.setItem(i, 0, QTableWidgetItem(os.path.basename(p)))
            it = QTableWidgetItem(n)
            if len(set(names)) != len(names) and names.count(n) > 1:
                it.setForeground(QColor("red"))
            self.preview.setItem(i, 1, it)

    def run(self) -> dict[str, str]:
        names = self.new_names()
        if len(set(names)) != len(names):
            QMessageBox.warning(self, "Renombrar", "La plantilla genera nombres repetidos.")
            return {}
        mapping: dict[str, str] = {}
        # Renombrado en dos fases para permitir intercambios de nombres.
        temps = []
        for p in self.paths:
            t = p + ".rvren"
            os.rename(p, t)
            temps.append(t)
        errors = []
        for p, t, n in zip(self.paths, temps, names):
            dest = os.path.join(os.path.dirname(p), n)
            if os.path.exists(dest):
                errors.append(f"{n}: ya existe")
                os.rename(t, p)
                continue
            os.rename(t, dest)
            mapping[p] = dest
            from .fileops import move_description

            move_description(p, dest)
        if errors:
            QMessageBox.warning(self.parentWidget(), "Renombrar", "No se renombraron:\n" + "\n".join(errors[:20]))
        return mapping


class FolderPickDialog(QDialog):
    """'Copiar a carpeta' / 'Mover a carpeta' con lista de carpetas recientes."""

    def __init__(self, move: bool, start: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Mover a carpeta" if move else "Copiar a carpeta")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Carpeta de destino:"))
        row = QHBoxLayout()
        self.edit = QLineEdit(start)
        b = QPushButton("Examinar...")
        b.clicked.connect(self._pick)
        row.addWidget(self.edit)
        row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(QLabel("Carpetas recientes:"))
        self.recent = QListWidget()
        for d in settings().get("general/recent_copy_dirs"):
            if os.path.isdir(d):
                self.recent.addItem(d)
        self.recent.currentTextChanged.connect(self.edit.setText)
        self.recent.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.recent)
        lay.addWidget(_buttons(self, "Mover" if move else "Copiar"))
        self.resize(480, 340)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Elegir carpeta", self.edit.text() or str(Path.home()))
        if d:
            self.edit.setText(d)

    def folder(self) -> str:
        d = self.edit.text().strip()
        rec = [x for x in settings().get("general/recent_copy_dirs") if x != d]
        settings().set("general/recent_copy_dirs", [d] + rec[:9])
        return d


class FilterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        s = settings()
        self.setWindowTitle("Filtro")
        lay = QVBoxLayout(self)
        self.r_all = QRadioButton("Mostrar todos los archivos")
        self.r_img = QRadioButton("Mostrar sólo imágenes")
        self.r_cus = QRadioButton("Filtro personalizado (separe con ;):")
        self.custom = QLineEdit(s.get("browser/filter_custom"))
        {"all": self.r_all, "images": self.r_img, "custom": self.r_cus}.get(s.get("browser/filter"), self.r_all).setChecked(True)
        for w in (self.r_all, self.r_img, self.r_cus, self.custom):
            lay.addWidget(w)
        self.hidden = QCheckBox("Mostrar archivos ocultos")
        self.hidden.setChecked(s.get("browser/show_hidden"))
        self.folders = QCheckBox("Mostrar carpetas")
        self.folders.setChecked(s.get("browser/show_folders"))
        lay.addWidget(self.hidden)
        lay.addWidget(self.folders)
        lay.addWidget(_buttons(self))

    def accept(self):
        s = settings()
        s.set("browser/filter", "images" if self.r_img.isChecked() else "custom" if self.r_cus.isChecked() else "all")
        s.set("browser/filter_custom", self.custom.text())
        s.set("browser/show_hidden", self.hidden.isChecked())
        s.set("browser/show_folders", self.folders.isChecked())
        super().accept()


def match_filter(name: str) -> bool:
    s = settings()
    f = s.get("browser/filter")
    if f == "images":
        return imaging.is_image(name)
    if f == "custom":
        pats = [p.strip() for p in s.get("browser/filter_custom").split(";") if p.strip()]
        return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in pats) if pats else True
    return True


class FavoritesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Organizar favoritos")
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        self.list.addItems(settings().get("general/favorites"))
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        lay.addWidget(self.list)
        col = QVBoxLayout()
        add = QPushButton("Añadir...")
        rem = QPushButton("Quitar")
        up = QPushButton("Subir")
        down = QPushButton("Bajar")
        add.clicked.connect(self._add)
        rem.clicked.connect(lambda: self.list.takeItem(self.list.currentRow()))
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        for b in (add, rem, up, down):
            col.addWidget(b)
        col.addStretch()
        col.addWidget(_buttons(self))
        lay.addLayout(col)
        self.resize(520, 300)

    def _add(self):
        d = QFileDialog.getExistingDirectory(self, "Añadir carpeta favorita", str(Path.home()))
        if d:
            self.list.addItem(d)

    def _move(self, delta):
        r = self.list.currentRow()
        if r < 0 or not (0 <= r + delta < self.list.count()):
            return
        it = self.list.takeItem(r)
        self.list.insertItem(r + delta, it)
        self.list.setCurrentRow(r + delta)

    def accept(self):
        settings().set("general/favorites", [self.list.item(i).text() for i in range(self.list.count())])
        super().accept()


# =============================================================== impresión

class PrintSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        s = settings()
        self.setWindowTitle("Imprimir")
        lay = QVBoxLayout(self)
        g = QGroupBox("Tamaño de la imagen")
        gl = QGridLayout(g)
        self.r_fit = QRadioButton("Ajustar a la página")
        self.r_act = QRadioButton("Tamaño real (según los ppp de la imagen)")
        self.r_sca = QRadioButton("Escala:")
        self.scale = QSpinBox()
        self.scale.setRange(1, 1000)
        self.scale.setSuffix(" %")
        self.scale.setValue(s.get("print/scale"))
        {"fit": self.r_fit, "actual": self.r_act, "scale": self.r_sca}.get(s.get("print/mode"), self.r_fit).setChecked(True)
        gl.addWidget(self.r_fit, 0, 0, 1, 2)
        gl.addWidget(self.r_act, 1, 0, 1, 2)
        gl.addWidget(self.r_sca, 2, 0)
        gl.addWidget(self.scale, 2, 1)
        lay.addWidget(g)
        self.caption = QCheckBox("Imprimir el nombre del archivo")
        self.caption.setChecked(s.get("print/caption"))
        lay.addWidget(self.caption)
        lay.addWidget(_buttons(self, "Vista previa..."))

    def accept(self):
        s = settings()
        s.set("print/mode", "fit" if self.r_fit.isChecked() else "actual" if self.r_act.isChecked() else "scale")
        s.set("print/scale", self.scale.value())
        s.set("print/caption", self.caption.isChecked())
        super().accept()


def print_images(parent, items: list[tuple[QImage, str, float]]) -> None:
    """items: (imagen, nombre, ppp). Muestra opciones, vista previa e imprime."""
    if not items:
        return
    if PrintSetupDialog(parent).exec() != QDialog.Accepted:
        return
    s = settings()
    printer = QPrinter(QPrinter.HighResolution)
    printer.setDocName(items[0][1])

    def render(pr: QPrinter):
        p = QPainter(pr)
        for idx, (img, name, dpi) in enumerate(items):
            if idx:
                pr.newPage()
            page = pr.pageRect(QPrinter.DevicePixel)
            area = page.adjusted(0, 0, 0, 0)
            area.moveTo(0, 0)
            cap_h = 0
            if s.get("print/caption"):
                cap_h = int(pr.resolution() * 0.3)
            mode = s.get("print/mode")
            w, h = img.width(), img.height()
            if mode == "fit":
                sc = min(area.width() / w, (area.height() - cap_h) / h)
            elif mode == "actual":
                sc = pr.resolution() / (dpi or 96.0)
            else:
                sc = pr.resolution() / 96.0 * s.get("print/scale") / 100
            tw, th = w * sc, h * sc
            x = (area.width() - tw) / 2
            y = (area.height() - cap_h - th) / 2
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            from PySide6.QtCore import QRectF

            p.drawImage(QRectF(x, y, tw, th), img)
            if cap_h:
                f = p.font()
                f.setPointSize(10)
                p.setFont(f)
                p.drawText(QRectF(0, area.height() - cap_h, area.width(), cap_h), Qt.AlignCenter, name)
        p.end()

    pv = QPrintPreviewDialog(printer, parent)
    pv.setWindowTitle("Vista previa de impresión")
    pv.paintRequested.connect(render)
    pv.resize(900, 700)
    pv.exec()


# =============================================================== ayuda

SHORTCUTS_BROWSER = [
    ("Intro / doble clic", "Ver la imagen"), ("Retroceso", "Carpeta superior"),
    ("Alt+← / Alt+→", "Atrás / Adelante"), ("F5", "Actualizar"), ("F2", "Renombrar"),
    ("Supr", "Eliminar (papelera)"), ("Mayús+Supr", "Eliminar definitivamente"),
    ("Ctrl+A", "Seleccionar todo"), ("Ctrl+C / Ctrl+X / Ctrl+V", "Copiar / cortar / pegar archivos"),
    ("Alt+Intro", "Propiedades"), ("Ctrl+R", "Rotar / voltear"), ("Ctrl+F", "Convertir formato"),
    ("Ctrl+E", "Editar con programa externo"), ("Ctrl+G", "Ir a carpeta"), ("Ctrl+D", "Añadir a favoritos"),
    ("Ctrl+Mayús+D", "Modo oscuro"), ("Ctrl+1 … Ctrl+5", "Modo de vista"), ("Ctrl+S", "Presentación"),
    ("Ctrl+P", "Imprimir"), ("Ctrl+M / Ctrl+Mayús+M", "Mover / copiar a carpeta"),
]
SHORTCUTS_VIEWER = [
    ("Av Pág / Espacio / → ↓", "Imagen siguiente"), ("Re Pág / Retroceso / ← ↑", "Imagen anterior"),
    ("Inicio / Fin", "Primera / última imagen"), ("Intro", "Volver al explorador"), ("Esc", "Salir de pantalla completa / volver"),
    ("F / F11 / doble clic", "Pantalla completa"), ("+ / -", "Acercar / alejar"),
    ("/", "Tamaño real"), ("*", "Ajustar a la ventana"), ("Ctrl+L", "Bloquear zoom"),
    ("[ / ]", "Rotar izquierda / derecha"), ("Ctrl+H / Ctrl+Mayús+V", "Voltear horizontal / vertical"),
    ("Ctrl+C", "Copiar imagen"), ("Ctrl+S", "Guardar como"), ("Ctrl+Mayús+S", "Guardar con MozJPEG"),
    ("Ctrl+Z", "Deshacer"), ("Ctrl+R", "Redimensionar"), ("Ctrl+J", "Ajustes de color"),
    ("Mayús+arrastrar", "Seleccionar zona"), ("Ctrl+X", "Recortar a la selección"),
    ("S", "Presentación automática"), ("Alt+Intro", "Propiedades"), ("Supr", "Eliminar"),
    ("Ctrl+P", "Imprimir"), ("Ctrl+W", "Fondo de escritorio"), ("Flechas (imagen ampliada)", "Desplazar la imagen"),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Atajos de teclado")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        for title, rows in (("Explorador", SHORTCUTS_BROWSER), ("Visor", SHORTCUTS_VIEWER)):
            t = QTableWidget(len(rows), 2)
            t.setHorizontalHeaderLabels(["Tecla", "Acción"])
            t.verticalHeader().hide()
            t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            t.setEditTriggers(QAbstractItemView.NoEditTriggers)
            for i, (k, a) in enumerate(rows):
                t.setItem(i, 0, QTableWidgetItem(k))
                t.setItem(i, 1, QTableWidgetItem(a))
            t.resizeColumnToContents(0)
            tabs.addTab(t, title)
        lay.addWidget(tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("Cerrar")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def about(parent):
    from . import __version__

    QMessageBox.about(parent, "Acerca de RetroVisor",
                      f"<h3>RetroVisor {__version__}</h3>"
                      "<p>Visor y explorador de imágenes rápido, con la interfaz clásica de los "
                      "visores de finales de los 90.</p>"
                      "<p>Incluye modo oscuro y guardado en formato JPEG optimizado con MozJPEG.</p>"
                      "<p>Basado en Qt, Pillow y MozJPEG.<br>Software libre.</p>")
