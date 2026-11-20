#!/usr/bin/env bash
# Construye RetroVisor-x86_64.AppImage.
#
# Requisitos (Linux Mint / Ubuntu):
#   sudo apt install python3-venv python3-dev build-essential cmake nasm \
#        squashfs-tools libsquashfuse-dev zlib1g-dev libzstd-dev liblzma-dev liblz4-dev liblzo2-dev \
#        libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0
#
# Uso:  ./build-appimage.sh            (deja el resultado en dist/)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
APPDIR="$BUILD/AppDir"
VENV="$BUILD/venv"
PY="${PYTHON:-python3}"
MOZJPEG_SDIST_VERSION="1.3.3"   # mozjpeg-lossless-optimization (incluye el código fuente de MozJPEG)

mkdir -p "$BUILD" "$DIST"

echo "==> Entorno de Python"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$ROOT/requirements.txt" pyinstaller

echo "==> MozJPEG (cjpeg y jpegtran estáticos)"
BIN="$ROOT/retrovisor/bin"
if [ ! -x "$BIN/cjpeg" ] || [ ! -x "$BIN/jpegtran" ]; then
    SRC="$BUILD/mozjpeg-src"
    rm -rf "$SRC" "$BUILD/mozjpeg-build"
    mkdir -p "$SRC"
    "$VENV/bin/pip" download -q --no-deps --no-binary :all: \
        "mozjpeg-lossless-optimization==$MOZJPEG_SDIST_VERSION" -d "$SRC"
    tar xzf "$SRC"/mozjpeg_lossless_optimization-*.tar.gz -C "$SRC"
    MOZ="$(echo "$SRC"/mozjpeg_lossless_optimization-*/mozjpeg)"
    mkdir -p "$BUILD/mozjpeg-build"
    (cd "$BUILD/mozjpeg-build" && \
        cmake -DBUILD_SHARED_LIBS=FALSE -DPNG_SUPPORTED=FALSE -DWITH_TURBOJPEG=FALSE \
              -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXE_LINKER_FLAGS=-static "$MOZ" >/dev/null && \
        make -j"$(nproc)" cjpeg-static jpegtran-static >/dev/null)
    mkdir -p "$BIN"
    cp "$BUILD/mozjpeg-build/cjpeg-static" "$BIN/cjpeg"
    cp "$BUILD/mozjpeg-build/jpegtran-static" "$BIN/jpegtran"
    strip "$BIN/cjpeg" "$BIN/jpegtran"
fi
"$BIN/cjpeg" -version

echo "==> Congelando la aplicación con PyInstaller"
rm -rf "$BUILD/pyi" "$APPDIR"
"$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
    --name retrovisor --windowed --onedir \
    --distpath "$BUILD/pyi/dist" --workpath "$BUILD/pyi/work" --specpath "$BUILD/pyi" \
    --add-data "$ROOT/retrovisor/icons:icons" \
    --add-binary "$BIN/cjpeg:bin" --add-binary "$BIN/jpegtran:bin" \
    --hidden-import PySide6.QtSvg --hidden-import PySide6.QtPrintSupport \
    --collect-submodules pillow_heif \
    --exclude-module tkinter --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml \
    --exclude-module PySide6.QtQuick --exclude-module PySide6.QtWebEngineCore \
    --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia \
    "$ROOT/retrovisor/__main__.py"

echo "==> Quitando librerías que siempre aporta el sistema (evita conflictos con Mint)"
INT="$BUILD/pyi/dist/retrovisor/_internal"
# Plugins de Qt que no hacen falta en un escritorio (y que arrastran GTK, EGL, etc.)
rm -rf "$INT/PySide6/Qt/plugins/egldeviceintegrations" "$INT/PySide6/Qt/plugins/generic" \
       "$INT/PySide6/Qt/plugins/platformthemes"
for p in libqeglfs libqlinuxfb libqminimal libqminimalegl libqvkkhrdisplay libqvnc; do
    rm -f "$INT/PySide6/Qt/plugins/platforms/$p.so"
done
# Lista de exclusión (misma idea que la «excludelist» de AppImage): bibliotecas presentes en
# cualquier instalación de escritorio de Mint que no conviene llevar dentro del paquete.
EXCLUDE=(
  'libstdc++.so.*' 'libgcc_s.so.*' 'libmvec.so.*' 'libsystemd.so.*' 'libselinux.so.*' 'libblkid.so.*'
  'libmount.so.*' 'libcap.so.*' 'libgcrypt.so.*' 'libgpg-error.so.*' 'libdbus-1.so.*'
  'libfontconfig.so.*' 'libfreetype.so.*' 'libharfbuzz.so.*' 'libexpat.so.*' 'libz.so.*'
  'libX11.so.*' 'libX11-xcb.so.*' 'libXau.so.*' 'libXdmcp.so.*' 'libXext.so.*' 'libXrender.so.*'
  'libXcomposite.so.*' 'libXcursor.so.*' 'libXdamage.so.*' 'libXfixes.so.*' 'libXi.so.*'
  'libXinerama.so.*' 'libXrandr.so.*'
  'libcups.so.*' 'libgnutls.so.*' 'libkrb5.so.*' 'libk5crypto.so.*' 'libgssapi_krb5.so.*'
  'libcom_err.so.*' 'libkeyutils.so.*' 'libkrb5support.so.*' 'libavahi-*.so.*' 'libp11-kit.so.*'
  'libtasn1.so.*' 'libidn2.so.*' 'libunistring.so.*' 'libnettle.so.*' 'libhogweed.so.*' 'libgmp.so.*'
  'libglib-2.0.so.*' 'libgio-2.0.so.*' 'libgobject-2.0.so.*' 'libgmodule-2.0.so.*' 'libgthread-2.0.so.*'
  'libpcre2-8.so.*' 'libgtk-3.so.*' 'libgdk-3.so.*' 'libgdk_pixbuf-2.0.so.*' 'libpango*.so.*'
  'libcairo*.so.*' 'libatk*.so.*' 'libatspi.so.*' 'libepoxy.so.*' 'libpixman-1.so.*' 'libfribidi.so.*'
  'libthai.so.*' 'libdatrie.so.*' 'libgraphite2.so.*' 'libpng16.so.*' 'libbz2.so.*' 'libbrotli*.so.*'
  'libbsd.so.*' 'libmd.so.*' 'libjpeg.so.8' 'liblzma.so.5' 'libzstd.so.1' 'liblz4.so.*'
  'libxcb-glx.so.*' 'libxcb-render.so.*' 'libxcb-shm.so.*' 'libxcb-sync.so.*' 'libxcb-xfixes.so.*'
  'libxcb-randr.so.*' 'libxcb-shape.so.*' 'libxcb-xkb.so.*'
)
for pat in "${EXCLUDE[@]}"; do
    find "$INT" -maxdepth 1 -name "$pat" -delete
done

echo "==> Bibliotecas X11 de Ubuntu 22.04 (compatibilidad con Mint 21.x, glibc 2.35)"
# Las versiones de Ubuntu 24.04 exigen glibc 2.38; estas funcionan igual en Mint 21 y 22.
DEBS="$BUILD/debs"
mkdir -p "$DEBS"
fetch_deb() {  # ruta-en-pool sha256
    local f="$DEBS/$(basename "$1")"
    [ -f "$f" ] || curl -fsSL -o "$f" "http://archive.ubuntu.com/ubuntu/pool/$1"
    echo "$2  $f" | sha256sum -c --quiet -
    dpkg-deb -x "$f" "$DEBS/x"
}
fetch_deb universe/x/xcb-util-cursor/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb \
    c9b5d1ad4af57397b1bd77e0a92750e34419def134c0282a0836ae9efc07cf64
fetch_deb main/libx/libxkbcommon/libxkbcommon0_1.4.0-1_amd64.deb \
    a0470edb237d52b71ad41f755c043164abed01423585ed5ea19525b3baaf84f7
fetch_deb main/libx/libxkbcommon/libxkbcommon-x11-0_1.4.0-1_amd64.deb \
    e29c40fab6e0f055218aee4c7e60cd61ea82283229d7ac7ac02e226e92a5b0d5
for lib in libxcb-cursor.so.0 libxkbcommon.so.0 libxkbcommon-x11.so.0; do
    rm -f "$INT/$lib"
    cp -L "$DEBS/x/usr/lib/x86_64-linux-gnu/$lib" "$INT/$lib"
done

echo "==> Montando AppDir"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/icons/hicolor/scalable/apps"
cp -a "$BUILD/pyi/dist/retrovisor" "$APPDIR/usr/lib/retrovisor"
cp "$BIN/cjpeg" "$BIN/jpegtran" "$APPDIR/usr/bin/"
cp "$ROOT/packaging/AppRun" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp "$ROOT/packaging/retrovisor.desktop" "$APPDIR/retrovisor.desktop"
cp "$ROOT/packaging/retrovisor.desktop" "$APPDIR/usr/share/applications/"
cp "$ROOT/retrovisor/icons/retrovisor.png" "$APPDIR/retrovisor.png"
cp "$ROOT/retrovisor/icons/retrovisor.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"
cp "$ROOT/retrovisor/icons/retrovisor.svg" "$APPDIR/usr/share/icons/hicolor/scalable/apps/"
ln -sf retrovisor.png "$APPDIR/.DirIcon"

echo "==> Runtime del AppImage"
gcc -O2 -static -Wno-format-truncation -o "$BUILD/runtime" "$ROOT/appimage/runtime.c" \
    -I/usr/include/squashfuse -lsquashfuse -lz -lzstd -llzma -llz4 -llzo2
strip "$BUILD/runtime"

echo "==> Imagen SquashFS"
rm -f "$BUILD/app.squashfs"
mksquashfs "$APPDIR" "$BUILD/app.squashfs" -root-owned -noappend -comp zstd -Xcompression-level 19 \
    -b 1M -quiet

OUT="$DIST/RetroVisor-x86_64.AppImage"
cat "$BUILD/runtime" "$BUILD/app.squashfs" > "$OUT"
# Firma mágica de AppImage tipo 2: "AI\x02" en el desplazamiento 8 de la cabecera ELF.
printf 'AI\x02' | dd of="$OUT" bs=1 seek=8 count=3 conv=notrunc status=none
chmod +x "$OUT"
echo
echo "Listo: $OUT ($(du -h "$OUT" | cut -f1))"
