"""Aspecto: estilo clásico (Windows 9x) o Fusion, modo claro/oscuro e iconos."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory

from .settings import settings
from .util import run_host

_system_icon_theme: str | None = None


def _detect_icon_theme() -> str:
    """Lee el tema de iconos del escritorio (Cinnamon, MATE, GNOME, Xfce)."""
    for schema, key in (("org.cinnamon.desktop.interface", "icon-theme"),
                        ("org.mate.interface", "icon-theme"),
                        ("org.gnome.desktop.interface", "icon-theme")):
        try:
            r = run_host(["gsettings", "get", schema, key], timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().strip("'")
        except Exception:
            continue
    return ""


def _icon_theme_exists(name: str) -> bool:
    dirs = [Path.home() / ".icons", Path.home() / ".local/share/icons", Path("/usr/share/icons"),
            Path("/usr/local/share/icons")]
    return any((d / name / "index.theme").exists() for d in dirs)


def system_prefers_dark() -> bool:
    try:
        hints = QApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pass
    for schema, key in (("org.cinnamon.desktop.interface", "gtk-theme"),
                        ("org.gnome.desktop.interface", "gtk-theme"),
                        ("org.gnome.desktop.interface", "color-scheme")):
        try:
            r = run_host(["gsettings", "get", schema, key], timeout=3)
            if "dark" in r.stdout.lower():
                return True
        except Exception:
            continue
    return False


def is_dark() -> bool:
    t = settings().get("appearance/theme")
    if t == "dark":
        return True
    if t == "system":
        return system_prefers_dark()
    return False


def dark_palette() -> QPalette:
    p = QPalette()
    base = QColor(37, 37, 38)
    window = QColor(45, 45, 48)
    text = QColor(220, 220, 220)
    disabled = QColor(120, 120, 120)
    hl = QColor(42, 98, 170)
    p.setColor(QPalette.Window, window)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(52, 52, 56))
    p.setColor(QPalette.ToolTipBase, QColor(60, 60, 64))
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.PlaceholderText, disabled)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor(55, 55, 58))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.Light, QColor(80, 80, 84))
    p.setColor(QPalette.Midlight, QColor(66, 66, 70))
    p.setColor(QPalette.Mid, QColor(40, 40, 42))
    p.setColor(QPalette.Dark, QColor(25, 25, 26))
    p.setColor(QPalette.Shadow, QColor(10, 10, 10))
    p.setColor(QPalette.Highlight, hl)
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, QColor(100, 160, 240))
    p.setColor(QPalette.LinkVisited, QColor(160, 120, 230))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(70, 70, 74))
    return p


def classic_palette() -> QPalette:
    """Paleta gris clásica de Windows 98 (la que usaban los visores de la época)."""
    p = QPalette()
    face = QColor(192, 192, 192)
    p.setColor(QPalette.Window, face)
    p.setColor(QPalette.WindowText, Qt.black)
    p.setColor(QPalette.Base, Qt.white)
    p.setColor(QPalette.AlternateBase, QColor(240, 240, 240))
    p.setColor(QPalette.ToolTipBase, QColor(255, 255, 225))
    p.setColor(QPalette.ToolTipText, Qt.black)
    p.setColor(QPalette.Text, Qt.black)
    p.setColor(QPalette.Button, face)
    p.setColor(QPalette.ButtonText, Qt.black)
    p.setColor(QPalette.Light, Qt.white)
    p.setColor(QPalette.Midlight, QColor(223, 223, 223))
    p.setColor(QPalette.Mid, QColor(160, 160, 160))
    p.setColor(QPalette.Dark, QColor(128, 128, 128))
    p.setColor(QPalette.Shadow, Qt.black)
    p.setColor(QPalette.Highlight, QColor(0, 0, 128))
    p.setColor(QPalette.HighlightedText, Qt.white)
    p.setColor(QPalette.PlaceholderText, QColor(128, 128, 128))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(128, 128, 128))
    return p


def apply_theme(app: QApplication | None = None) -> None:
    global _system_icon_theme
    app = app or QApplication.instance()
    s = settings()
    style_name = s.get("appearance/style")
    if style_name not in QStyleFactory.keys():
        style_name = "Fusion"
    app.setStyle(QStyleFactory.create(style_name))
    dark = is_dark()
    if dark:
        app.setPalette(dark_palette())
    elif style_name == "Windows":
        app.setPalette(classic_palette())
    else:
        app.setPalette(app.style().standardPalette())

    if _system_icon_theme is None:
        _system_icon_theme = QIcon.themeName() if QIcon.themeName() not in ("", "hicolor") else _detect_icon_theme()
        paths = QIcon.themeSearchPaths()
        for extra in (str(Path.home() / ".icons"), str(Path.home() / ".local/share/icons"),
                      "/usr/share/icons", "/usr/local/share/icons"):
            if extra not in paths:
                paths.append(extra)
        QIcon.setThemeSearchPaths(paths)
        QIcon.setFallbackThemeName("Adwaita" if _icon_theme_exists("Adwaita") else "hicolor")
    theme = _system_icon_theme or ""
    if dark and theme and "dark" not in theme.lower():
        for cand in (theme + "-Dark", theme + "-dark", theme.replace("-Light", "-Dark")):
            if _icon_theme_exists(cand):
                theme = cand
                break
    if not dark and theme.lower().endswith("-dark"):
        light = theme[:-5]
        if _icon_theme_exists(light):
            theme = light
    if theme:
        QIcon.setThemeName(theme)
    _icon_cache.clear()
    for w in app.topLevelWidgets():
        if hasattr(w, "on_theme_changed"):
            w.on_theme_changed()


# ----------------------------------------------------------------------------- iconos

_icon_cache: dict[str, QIcon] = {}

_ICONS = {
    # nombre lógico: (nombres freedesktop, icono estándar Qt de respaldo)
    "view": (["image-x-generic", "view-preview", "eog"], QStyle.SP_FileIcon),
    "browse": (["system-file-manager", "folder-open"], QStyle.SP_DirOpenIcon),
    "up": (["go-up", "go-parent-folder"], QStyle.SP_FileDialogToParent),
    "back": (["go-previous"], QStyle.SP_ArrowBack),
    "forward": (["go-next"], QStyle.SP_ArrowForward),
    "prev": (["go-previous", "media-skip-backward"], QStyle.SP_ArrowLeft),
    "next": (["go-next", "media-skip-forward"], QStyle.SP_ArrowRight),
    "first": (["go-first"], QStyle.SP_MediaSkipBackward),
    "last": (["go-last"], QStyle.SP_MediaSkipForward),
    "home": (["go-home", "user-home"], QStyle.SP_DirHomeIcon),
    "refresh": (["view-refresh"], QStyle.SP_BrowserReload),
    "zoom-in": (["zoom-in"], None),
    "zoom-out": (["zoom-out"], None),
    "zoom-fit": (["zoom-fit-best"], None),
    "zoom-100": (["zoom-original"], None),
    "fullscreen": (["view-fullscreen"], QStyle.SP_TitleBarMaxButton),
    "slideshow": (["media-playback-start", "view-presentation"], QStyle.SP_MediaPlay),
    "pause": (["media-playback-pause"], QStyle.SP_MediaPause),
    "rotate-left": (["object-rotate-left"], None),
    "rotate-right": (["object-rotate-right"], None),
    "flip-h": (["object-flip-horizontal"], None),
    "flip-v": (["object-flip-vertical"], None),
    "delete": (["edit-delete", "user-trash"], QStyle.SP_TrashIcon),
    "properties": (["document-properties"], QStyle.SP_FileDialogInfoView),
    "print": (["document-print"], None),
    "save": (["document-save"], QStyle.SP_DialogSaveButton),
    "save-as": (["document-save-as"], QStyle.SP_DialogSaveButton),
    "open": (["document-open"], QStyle.SP_DialogOpenButton),
    "copy": (["edit-copy"], None),
    "cut": (["edit-cut"], None),
    "paste": (["edit-paste"], None),
    "rename": (["edit-rename", "document-edit"], None),
    "copy-to": (["edit-copy"], None),
    "move-to": (["go-jump", "edit-cut"], None),
    "edit": (["accessories-image-editor", "image-x-generic", "gimp"], None),
    "wallpaper": (["preferences-desktop-wallpaper", "user-desktop"], QStyle.SP_DesktopIcon),
    "convert": (["document-export", "document-save-as"], QStyle.SP_DialogSaveButton),
    "resize": (["transform-scale", "zoom-fit-best"], None),
    "acquire": (["scanner", "document-import"], None),
    "folder": (["folder"], QStyle.SP_DirIcon),
    "folder-new": (["folder-new"], QStyle.SP_FileDialogNewFolder),
    "favorites": (["starred", "emblem-favorite", "bookmarks"], None),
    "thumbs": (["view-grid", "view-list-icons"], QStyle.SP_FileDialogContentsView),
    "details": (["view-list-details"], QStyle.SP_FileDialogDetailedView),
    "list": (["view-list-text", "view-list"], QStyle.SP_FileDialogListView),
    "preview": (["view-preview", "image-x-generic"], None),
    "tree": (["view-list-tree", "folder"], QStyle.SP_DirIcon),
    "help": (["help-contents", "help-browser"], QStyle.SP_DialogHelpButton),
    "options": (["preferences-system", "configure", "preferences-other"], None),
    "exit": (["application-exit", "window-close"], QStyle.SP_DialogCloseButton),
    "undo": (["edit-undo"], None),
    "redo": (["edit-redo"], None),
    "crop": (["transform-crop", "image-crop"], None),
    "adjust": (["color-management", "preferences-color"], None),
    "terminal": (["utilities-terminal"], QStyle.SP_ComputerIcon),
    "dark": (["weather-clear-night", "night-light"], None),
    "filter": (["view-filter", "edit-find"], None),
    "sort": (["view-sort-ascending"], None),
    "info": (["dialog-information"], QStyle.SP_MessageBoxInformation),
    "find": (["edit-find", "system-search"], None),
}


def _draw_icon(name: str) -> QIcon:
    """Iconos dibujados para cuando el tema del sistema no los tiene."""
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    fg = QApplication.palette().color(QPalette.WindowText)
    accent = QColor(30, 110, 210)
    pen = QPen(fg, 2.6)
    p.setPen(pen)
    if name.startswith("zoom"):
        p.setBrush(QColor(200, 225, 255, 160))
        p.drawEllipse(QRectF(4, 4, 18, 18))
        p.setPen(QPen(fg, 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(20, 20), QPointF(28, 28))
        p.setPen(QPen(fg, 2.4))
        if name in ("zoom-in", "zoom-out"):
            p.drawLine(QPointF(8, 13), QPointF(18, 13))
        if name == "zoom-in":
            p.drawLine(QPointF(13, 8), QPointF(13, 18))
        if name == "zoom-fit":
            p.drawRect(QRectF(9, 9, 8, 8))
        if name == "zoom-100":
            p.setFont(p.font())
            f = p.font(); f.setPixelSize(10); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(4, 4, 18, 18), Qt.AlignCenter, "1:1")
    elif name in ("rotate-left", "rotate-right"):
        p.setPen(QPen(accent, 3))
        p.drawArc(QRectF(6, 6, 20, 20), 30 * 16 if name == "rotate-right" else 150 * 16,
                  270 * 16 if name == "rotate-right" else -270 * 16)
        p.setBrush(accent)
        if name == "rotate-right":
            p.drawPolygon([QPointF(24, 2), QPointF(29, 12), QPointF(19, 11)])
        else:
            p.drawPolygon([QPointF(8, 2), QPointF(3, 12), QPointF(13, 11)])
    elif name in ("flip-h", "flip-v"):
        p.setBrush(accent)
        if name == "flip-h":
            p.drawPolygon([QPointF(14, 6), QPointF(14, 26), QPointF(3, 26)])
            p.setBrush(Qt.NoBrush)
            p.drawPolygon([QPointF(18, 6), QPointF(18, 26), QPointF(29, 26)])
        else:
            p.drawPolygon([QPointF(6, 14), QPointF(26, 14), QPointF(26, 3)])
            p.setBrush(Qt.NoBrush)
            p.drawPolygon([QPointF(6, 18), QPointF(26, 18), QPointF(26, 29)])
    elif name == "print":
        p.setBrush(QColor(230, 230, 230))
        p.drawRect(QRectF(9, 3, 14, 10))
        p.setBrush(QColor(150, 150, 150))
        p.drawRect(QRectF(3, 12, 26, 12))
        p.setBrush(Qt.white)
        p.drawRect(QRectF(9, 20, 14, 9))
    elif name in ("copy", "copy-to", "paste", "cut", "move-to"):
        p.setBrush(QColor(250, 250, 250))
        p.drawRect(QRectF(4, 4, 15, 19))
        p.drawRect(QRectF(13, 9, 15, 19))
        if name in ("move-to", "cut"):
            p.setPen(QPen(accent, 3))
            p.drawLine(QPointF(6, 28), QPointF(14, 20))
    elif name == "favorites":
        p.setBrush(QColor(250, 200, 40))
        import math

        pts = []
        for i in range(10):
            r = 14 if i % 2 == 0 else 6
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(QPointF(16 + r * math.cos(a), 17 + r * math.sin(a)))
        p.drawPolygon(pts)
    elif name == "dark":
        p.setBrush(fg)
        p.drawEllipse(QRectF(5, 5, 22, 22))
        p.setBrush(QApplication.palette().color(QPalette.Window))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(12, 2, 20, 20))
    else:
        p.setBrush(QColor(200, 220, 250))
        p.drawRoundedRect(QRectF(4, 6, 24, 20), 3, 3)
        p.setPen(QPen(accent, 2))
        p.drawLine(QPointF(7, 22), QPointF(14, 14))
        p.drawLine(QPointF(14, 14), QPointF(19, 19))
        p.drawLine(QPointF(19, 19), QPointF(25, 12))
    p.end()
    return QIcon(pm)


def _lighten_dark_pixels(ic: QIcon) -> QIcon:
    """Los iconos estándar de Qt son negros: en modo oscuro se aclaran para que se vean."""
    out = QIcon()
    for size in (16, 24, 32):
        img = ic.pixmap(size, size).toImage().convertToFormat(QImage.Format_ARGB32)
        for y in range(img.height()):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                if c.alpha() and c.lightness() < 90:
                    v = 230 - c.lightness()
                    img.setPixelColor(x, y, QColor(v, v, v, c.alpha()))
        out.addPixmap(QPixmap.fromImage(img))
    return out


def icon(name: str) -> QIcon:
    if name in _icon_cache:
        return _icon_cache[name]
    names, std = _ICONS.get(name, ([name], None))
    ic = QIcon()
    for n in names:
        if QIcon.hasThemeIcon(n):
            ic = QIcon.fromTheme(n)
            break
    if ic.isNull() and std is not None:
        ic = QApplication.style().standardIcon(std)
        if is_dark():
            ic = _lighten_dark_pixels(ic)
    if ic.isNull():
        ic = _draw_icon(name)
    _icon_cache[name] = ic
    return ic


def app_icon() -> QIcon:
    from .util import resource_dir

    for cand in (resource_dir() / "icons" / "retrovisor.svg", resource_dir() / "icons" / "retrovisor.png",
                 Path(os.environ.get("APPDIR", "/nonexistent")) / "retrovisor.png"):
        if cand.exists():
            return QIcon(str(cand))
    return icon("view")
