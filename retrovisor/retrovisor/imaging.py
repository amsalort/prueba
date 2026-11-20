"""Carga, información, conversión y guardado de imágenes (Pillow + MozJPEG)."""
from __future__ import annotations

import io
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ExifTags, Image, ImageOps, ImageSequence
from PySide6.QtGui import QImage

from .util import find_helper

Image.MAX_IMAGE_PIXELS = 400_000_000  # permitir panorámicas grandes

try:  # HEIC/HEIF de móviles (opcional)
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HAVE_HEIF = True
except Exception:  # pragma: no cover
    HAVE_HEIF = False

# Extensiones que se consideran imágenes en el explorador.
IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".bmp", ".dib", ".rle", ".tif", ".tiff",
    ".pcx", ".tga", ".icb", ".vda", ".vst", ".ico", ".cur", ".ppm", ".pgm", ".pbm", ".pnm",
    ".webp", ".psd", ".xbm", ".xpm", ".sgi", ".rgb", ".rgba", ".bw", ".ras", ".sun", ".dds",
    ".icns", ".jp2", ".j2k", ".jpx", ".jpf", ".avif", ".msp", ".fli", ".flc", ".wmf", ".emf",
    ".im", ".qoi", ".blp", ".ftc", ".dcx", ".svg", ".svgz",
}
if HAVE_HEIF:
    IMAGE_EXTS |= {".heic", ".heif", ".hif"}

FORMAT_NAMES = {
    "JPEG": "JPEG - Joint Photographic Experts Group",
    "PNG": "PNG - Portable Network Graphics",
    "GIF": "GIF - CompuServe Graphics Interchange",
    "BMP": "BMP - Mapa de bits de Windows",
    "TIFF": "TIFF - Tagged Image File Format",
    "PCX": "PCX - ZSoft Paintbrush",
    "TGA": "TGA - Truevision Targa",
    "ICO": "ICO - Icono de Windows",
    "PPM": "PPM/PGM/PBM - Portable Pixmap",
    "WEBP": "WebP - Google WebP",
    "PSD": "PSD - Photoshop",
    "SVG": "SVG - Gráficos vectoriales",
}

# Formatos de escritura: clave -> (nombre, extensión, formato Pillow)
SAVE_FORMATS = {
    "MOZJPEG": ("JPEG optimizado con MozJPEG", ".jpg", "JPEG"),
    "JPEG": ("JPEG - Joint Photographic Experts Group", ".jpg", "JPEG"),
    "PNG": ("PNG - Portable Network Graphics", ".png", "PNG"),
    "BMP": ("BMP - Mapa de bits de Windows", ".bmp", "BMP"),
    "GIF": ("GIF - CompuServe Graphics Interchange", ".gif", "GIF"),
    "TIFF": ("TIFF - Tagged Image File Format", ".tif", "TIFF"),
    "PCX": ("PCX - ZSoft Paintbrush", ".pcx", "PCX"),
    "TGA": ("TGA - Truevision Targa", ".tga", "TGA"),
    "WEBP": ("WebP - Google WebP", ".webp", "WEBP"),
    "PPM": ("PPM - Portable Pixmap", ".ppm", "PPM"),
    "ICO": ("ICO - Icono de Windows", ".ico", "ICO"),
}

EXT_TO_SAVE = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG", ".jfif": "JPEG", ".png": "PNG",
    ".bmp": "BMP", ".dib": "BMP", ".gif": "GIF", ".tif": "TIFF", ".tiff": "TIFF",
    ".pcx": "PCX", ".tga": "TGA", ".webp": "WEBP", ".ppm": "PPM", ".pgm": "PPM",
    ".pbm": "PPM", ".pnm": "PPM", ".ico": "ICO",
}

MODE_BPP = {"1": 1, "L": 8, "P": 8, "RGB": 24, "RGBA": 32, "CMYK": 32, "YCbCr": 24,
            "LAB": 24, "HSV": 24, "I": 32, "F": 32, "I;16": 16, "I;16B": 16, "I;16L": 16,
            "LA": 16, "PA": 16, "RGBX": 32, "RGBa": 32, "La": 16}


def is_image(path: str | os.PathLike) -> bool:
    return os.path.splitext(str(path))[1].lower() in IMAGE_EXTS


@dataclass
class Frame:
    image: QImage
    duration: int  # ms


@dataclass
class LoadedImage:
    path: str
    pil: Image.Image | None          # primer cuadro en modo RGB/RGBA/L (para editar/guardar)
    qimage: QImage                    # para mostrar
    frames: list[Frame] = field(default_factory=list)  # animación (si hay más de 1)
    width: int = 0
    height: int = 0
    bpp: int = 24
    format: str = ""
    mode: str = ""
    file_size: int = 0
    exif: bytes | None = None
    icc: bytes | None = None
    dpi: tuple | None = None
    load_ms: int = 0
    error: str = ""

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1


def pil_to_qimage(im: Image.Image) -> QImage:
    """Convierte una imagen Pillow en QImage (copia profunda)."""
    if im.mode == "1":
        im = im.convert("L")
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        im = _normalize_mode(im)
    if im.mode == "L":
        data = im.tobytes("raw", "L")
        qi = QImage(data, im.width, im.height, im.width, QImage.Format_Grayscale8)
        return qi.copy()
    if im.mode not in ("RGB", "RGBA"):
        has_alpha = im.mode in ("RGBA", "LA", "PA", "RGBa", "La") or (
            im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")
    if im.mode == "RGB":
        data = im.tobytes("raw", "RGB")
        qi = QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888)
    else:
        data = im.tobytes("raw", "RGBA")
        qi = QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888)
    return qi.copy()


def qimage_to_pil(qi: QImage) -> Image.Image:
    qi = qi.convertToFormat(QImage.Format_RGBA8888)
    w, h = qi.width(), qi.height()
    ptr = qi.constBits()
    data = bytes(ptr)[: qi.bytesPerLine() * h]
    im = Image.frombuffer("RGBA", (w, h), data, "raw", "RGBA", qi.bytesPerLine(), 1)
    if all(px == 255 for px in im.getchannel("A").getextrema()):
        im = im.convert("RGB")
    return im.copy()


def _normalize_mode(im: Image.Image) -> Image.Image:
    if im.mode in ("RGB", "RGBA", "L"):
        return im
    if im.mode == "P":
        return im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode in ("LA", "La", "PA", "RGBa"):
        return im.convert("RGBA")
    if im.mode == "CMYK":
        return im.convert("RGB")
    if im.mode.startswith("I;16") or im.mode in ("I", "F"):
        arr = im.convert("I")
        lo, hi = arr.getextrema()
        scale = 255.0 / max(1, hi - lo)
        return arr.point(lambda v: (v - lo) * scale).convert("L")
    if im.mode == "1":
        return im.convert("L")
    return im.convert("RGB")


def _load_svg(path: str) -> LoadedImage:
    from PySide6.QtGui import QImageReader

    reader = QImageReader(path)
    qi = reader.read()
    if qi.isNull():
        raise OSError(reader.errorString())
    return LoadedImage(path=path, pil=qimage_to_pil(qi), qimage=qi, width=qi.width(),
                       height=qi.height(), bpp=32, format="SVG", mode="RGBA",
                       file_size=os.path.getsize(path))


def load_image(path: str, autorotate: bool = False, max_frames: int = 400) -> LoadedImage:
    """Decodifica una imagen completa (todos los cuadros si es animada)."""
    import time

    t0 = time.perf_counter()
    if path.lower().endswith((".svg", ".svgz")):
        li = _load_svg(path)
        li.load_ms = int((time.perf_counter() - t0) * 1000)
        return li
    with Image.open(path) as src:
        fmt = src.format or ""
        mode = src.mode
        bpp = MODE_BPP.get(mode, 24)
        if mode == "P" and fmt in ("BMP", "PCX"):
            ncolors = len(src.getpalette() or []) // 3
            bpp = 8 if ncolors > 16 else (4 if ncolors > 2 else 1)
        info = dict(src.info)
        exif = info.get("exif")
        icc = info.get("icc_profile")
        dpi = info.get("dpi")
        n_frames = getattr(src, "n_frames", 1)
        frames: list[Frame] = []
        if n_frames > 1 and fmt in ("GIF", "WEBP", "PNG", "FLI", "AVIF"):
            for i, fr in enumerate(ImageSequence.Iterator(src)):
                if i >= max_frames:
                    break
                dur = int(fr.info.get("duration", 100) or 100)
                frames.append(Frame(pil_to_qimage(_normalize_mode(fr.copy())), max(dur, 20)))
            src.seek(0)
        im = src.copy() if n_frames == 1 else src.convert("RGBA" if src.mode in ("P", "RGBA", "LA", "PA") else "RGB")
        im.load()
    if autorotate and exif:
        im = ImageOps.exif_transpose(im)
    im = _normalize_mode(im)
    qi = frames[0].image if frames else pil_to_qimage(im)
    li = LoadedImage(path=path, pil=im, qimage=qi, frames=frames, width=im.width,
                     height=im.height, bpp=bpp, format=fmt, mode=mode,
                     file_size=os.path.getsize(path), exif=exif, icc=icc, dpi=dpi)
    li.load_ms = int((time.perf_counter() - t0) * 1000)
    return li


@dataclass
class ImageInfo:
    width: int = 0
    height: int = 0
    bpp: int = 0
    format: str = ""
    mode: str = ""
    frames: int = 1
    ok: bool = False


def read_info(path: str) -> ImageInfo:
    try:
        if path.lower().endswith((".svg", ".svgz")):
            from PySide6.QtGui import QImageReader

            r = QImageReader(path)
            s = r.size()
            return ImageInfo(s.width(), s.height(), 32, "SVG", "RGBA", 1, s.isValid())
        with Image.open(path) as im:
            return ImageInfo(im.width, im.height, MODE_BPP.get(im.mode, 24), im.format or "",
                             im.mode, getattr(im, "n_frames", 1), True)
    except Exception:
        return ImageInfo()


def make_thumbnail(path: str, size: int, autorotate: bool = False) -> tuple[QImage | None, ImageInfo]:
    """Miniatura rápida: usa draft() de JPEG para decodificar a escala reducida."""
    info = ImageInfo()
    try:
        if path.lower().endswith((".svg", ".svgz")):
            from PySide6.QtCore import QSize, Qt
            from PySide6.QtGui import QImageReader

            r = QImageReader(path)
            s = r.size()
            info = ImageInfo(s.width(), s.height(), 32, "SVG", "RGBA", 1, True)
            if s.isValid():
                r.setScaledSize(s.scaled(QSize(size, size), Qt.KeepAspectRatio))
            qi = r.read()
            return (None if qi.isNull() else qi), info
        with Image.open(path) as im:
            info = ImageInfo(im.width, im.height, MODE_BPP.get(im.mode, 24), im.format or "",
                             im.mode, getattr(im, "n_frames", 1), True)
            if im.format == "JPEG":
                im.draft("RGB", (size * 2, size * 2))
            orient = im.getexif().get(0x0112, 1) if autorotate else 1
            im.thumbnail((size, size), Image.Resampling.BILINEAR if max(im.size) > size * 4 else Image.Resampling.LANCZOS, reducing_gap=2.0)
            im = im.copy()
        method = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
                  4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
                  6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
                  8: Image.Transpose.ROTATE_90}.get(orient)
        if method is not None:
            im = im.transpose(method)
        return pil_to_qimage(_normalize_mode(im)), info
    except Exception:
        return None, info


def exif_table(path: str) -> list[tuple[str, str]]:
    """Etiquetas EXIF legibles para el diálogo de propiedades."""
    rows: list[tuple[str, str]] = []
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return rows
            ifds = [(exif, ExifTags.TAGS)]
            try:
                ifds.append((exif.get_ifd(ExifTags.IFD.Exif), ExifTags.TAGS))
                ifds.append((exif.get_ifd(ExifTags.IFD.GPSInfo), ExifTags.GPSTAGS))
            except Exception:
                pass
            for d, names in ifds:
                for tag, val in d.items():
                    name = names.get(tag, f"0x{tag:04X}")
                    if name in ("ExifOffset", "GPSInfo", "MakerNote", "UserComment") and isinstance(val, (bytes, int)):
                        continue
                    if isinstance(val, bytes):
                        val = val[:64].hex(" ") + ("…" if len(val) > 64 else "")
                    s = str(val)
                    if len(s) > 200:
                        s = s[:200] + "…"
                    rows.append((name, s))
    except Exception:
        pass
    return rows


# --------------------------------------------------------------------------- guardado

def mozjpeg_available() -> bool:
    return find_helper("cjpeg") is not None


def _insert_jpeg_segments(data: bytes, exif: bytes | None) -> bytes:
    """Inserta un APP1 Exif tras SOI/APP0 en un JPEG generado por cjpeg."""
    if not exif or data[:2] != b"\xff\xd8":
        return data
    if not exif.startswith(b"Exif\x00\x00"):
        exif = b"Exif\x00\x00" + exif
    if len(exif) + 2 > 0xFFFF:
        return data
    seg = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    pos = 2
    if data[2:4] == b"\xff\xe0":
        pos = 4 + int.from_bytes(data[4:6], "big")
    return data[:pos] + seg + data[pos:]


def encode_mozjpeg(im: Image.Image, quality: int = 80, progressive: bool = True,
                   trellis: bool = True, subsampling: str = "4:2:0", tune: str = "psnr-hvs",
                   exif: bytes | None = None, icc: bytes | None = None) -> bytes:
    """Codifica con el cjpeg de MozJPEG empaquetado (sin dependencias del sistema)."""
    cjpeg = find_helper("cjpeg")
    if im.mode not in ("RGB", "L"):
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            rgba = im.convert("RGBA")
            bg.paste(rgba, mask=rgba.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
    if cjpeg is None:
        # Respaldo: libjpeg de Pillow + optimización sin pérdida de MozJPEG si está.
        buf = io.BytesIO()
        kw = dict(quality=quality, optimize=True, progressive=progressive,
                  subsampling={"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}.get(subsampling, 2))
        if exif:
            kw["exif"] = exif
        if icc:
            kw["icc_profile"] = icc
        im.save(buf, "JPEG", **kw)
        data = buf.getvalue()
        try:
            import mozjpeg_lossless_optimization as mlo  # type: ignore

            data = mlo.optimize(data)
        except Exception:
            pass
        return data

    args = [cjpeg, "-quality", str(int(quality))]
    if not progressive:
        args.append("-baseline")
    if not trellis:
        args += ["-notrellis", "-notrellis-dc"]
    tune_flag = {"ssim": "-tune-ssim", "psnr": "-tune-psnr", "ms-ssim": "-tune-ms-ssim",
                 "psnr-hvs": "-tune-hvs-psnr"}.get(tune)
    if tune_flag:
        args.append(tune_flag)
    if im.mode == "RGB":
        args += ["-sample", {"4:4:4": "1x1", "4:2:2": "2x1", "4:2:0": "2x2"}.get(subsampling, "2x2")]
    icc_file = None
    try:
        if icc:
            fd, icc_file = tempfile.mkstemp(suffix=".icc")
            with os.fdopen(fd, "wb") as f:
                f.write(icc)
            args += ["-icc", icc_file]
        header = f"P{'5' if im.mode == 'L' else '6'}\n{im.width} {im.height}\n255\n".encode()
        proc = subprocess.run(args, input=header + im.tobytes(), capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            raise OSError("MozJPEG: " + proc.stderr.decode(errors="replace").strip())
        return _insert_jpeg_segments(proc.stdout, exif)
    finally:
        if icc_file:
            os.unlink(icc_file)


def save_image(im: Image.Image, path: str, fmt: str, opts: dict | None = None,
               exif: bytes | None = None, icc: bytes | None = None) -> None:
    """Guarda `im` en `path`. fmt es una clave de SAVE_FORMATS. Escritura atómica."""
    from .settings import settings

    s = settings()
    opts = dict(opts or {})
    fmt = fmt.upper()
    tmp = Path(path).with_name("." + Path(path).name + ".rvtmp")
    try:
        if fmt == "MOZJPEG":
            keep = opts.get("keep_exif", s.get("mozjpeg/keep_exif"))
            data = encode_mozjpeg(
                im, quality=opts.get("quality", s.get("mozjpeg/quality")),
                progressive=opts.get("progressive", s.get("mozjpeg/progressive")),
                trellis=opts.get("trellis", s.get("mozjpeg/trellis")),
                subsampling=opts.get("subsampling", s.get("mozjpeg/subsampling")),
                tune=opts.get("tune", s.get("mozjpeg/tune")),
                exif=exif if keep else None, icc=icc)
            tmp.write_bytes(data)
        else:
            pil_fmt = SAVE_FORMATS[fmt][2]
            kw: dict = {}
            out = im
            if fmt == "JPEG":
                kw = dict(quality=opts.get("quality", s.get("jpeg/quality")),
                          progressive=opts.get("progressive", s.get("jpeg/progressive")),
                          optimize=opts.get("optimize", s.get("jpeg/optimize")),
                          subsampling={"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}.get(
                              opts.get("subsampling", s.get("jpeg/subsampling")), 2))
                if exif and opts.get("keep_exif", s.get("jpeg/keep_exif")):
                    kw["exif"] = exif
                if icc:
                    kw["icc_profile"] = icc
                if out.mode not in ("RGB", "L"):
                    bg = Image.new("RGB", out.size, (255, 255, 255))
                    if out.mode in ("RGBA", "LA"):
                        bg.paste(out.convert("RGBA"), mask=out.convert("RGBA").getchannel("A"))
                        out = bg
                    else:
                        out = out.convert("RGB")
            elif fmt == "PNG":
                kw = dict(compress_level=opts.get("compress", s.get("png/compress")), optimize=False)
                if icc:
                    kw["icc_profile"] = icc
            elif fmt == "WEBP":
                kw = dict(quality=opts.get("quality", s.get("webp/quality")),
                          lossless=opts.get("lossless", s.get("webp/lossless")), method=4)
                if exif:
                    kw["exif"] = exif
            elif fmt == "TIFF":
                kw = dict(compression=opts.get("compression", s.get("tiff/compression")))
                if kw["compression"] == "none":
                    kw.pop("compression")
            elif fmt == "GIF":
                if out.mode not in ("P", "L"):
                    out = out.convert("RGB").quantize(256, dither=Image.Dither.FLOYDSTEINBERG) \
                        if out.mode != "RGBA" else out.quantize(255)
            elif fmt in ("BMP", "PCX", "PPM"):
                if out.mode not in ("RGB", "L", "P", "1"):
                    out = out.convert("RGB")
            elif fmt == "TGA":
                if out.mode not in ("RGB", "RGBA", "L", "P"):
                    out = out.convert("RGBA")
                kw = dict(compression="tga_rle") if opts.get("rle", True) else {}
            elif fmt == "ICO":
                if max(out.size) > 256:
                    out = out.copy()
                    out.thumbnail((256, 256), Image.Resampling.LANCZOS)
            out.save(str(tmp), pil_fmt, **kw)
        if Path(path).exists():
            try:
                os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def jpeg_lossless_transform(path: str, op: str, optimize_moz: bool = False) -> bool:
    """Rotación/volteo JPEG sin pérdida con jpegtran (MozJPEG). op: 90|180|270|horizontal|vertical|transpose|transverse|optimize."""
    jt = find_helper("jpegtran")
    if jt is None:
        return False
    args = [jt, "-copy", "all"]
    if op in ("90", "180", "270"):
        args += ["-rotate", op, "-trim"]
    elif op in ("horizontal", "vertical"):
        args += ["-flip", op, "-trim"]
    elif op in ("transpose", "transverse"):
        args += ["-" + op, "-trim"]
    if not optimize_moz:
        args += ["-optimize"]
    tmp = path + ".rvtmp"
    args += ["-outfile", tmp, path]
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False
    if op in ("90", "270", "transpose", "transverse"):
        _reset_exif_orientation(tmp)
    try:
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    except OSError:
        pass
    os.replace(tmp, path)
    return True


def _reset_exif_orientation(path: str) -> None:
    """Tras una rotación sin pérdida, pone la orientación EXIF a 1 (in situ)."""
    try:
        data = bytearray(Path(path).read_bytes())
        i = data.find(b"Exif\x00\x00")
        if i < 0 or i > 65536:
            return
        t = i + 6
        le = data[t:t + 2] == b"II"
        rd = (lambda o, n: int.from_bytes(data[o:o + n], "little" if le else "big"))
        ifd = t + rd(t + 4, 4)
        for k in range(rd(ifd, 2)):
            e = ifd + 2 + 12 * k
            if rd(e, 2) == 0x0112:
                data[e + 8:e + 10] = (1).to_bytes(2, "little" if le else "big")
                Path(path).write_bytes(bytes(data))
                return
    except Exception:
        pass


def mozjpeg_optimize_lossless(path: str) -> tuple[int, int]:
    """Re-comprime un JPEG sin pérdida con MozJPEG (jpegtran). Devuelve (antes, después)."""
    before = os.path.getsize(path)
    jt = find_helper("jpegtran")
    if jt:
        tmp = path + ".rvtmp"
        proc = subprocess.run([jt, "-copy", "all", "-outfile", tmp, path], capture_output=True)
        if proc.returncode == 0 and os.path.exists(tmp) and 0 < os.path.getsize(tmp) < before:
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            os.replace(tmp, path)
        elif os.path.exists(tmp):
            os.unlink(tmp)
    else:
        try:
            import mozjpeg_lossless_optimization as mlo  # type: ignore

            data = mlo.optimize(Path(path).read_bytes())
            if len(data) < before:
                Path(path).write_bytes(data)
        except Exception:
            pass
    return before, os.path.getsize(path)
