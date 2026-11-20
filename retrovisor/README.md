# RetroVisor

Visor y explorador de imágenes para Linux Mint con la interfaz y el modo de trabajo de los visores
clásicos de Windows de 1999–2000 (explorador con árbol de carpetas + miniaturas + vista previa, y
visor a ventana completa que se abre con Intro y vuelve al explorador con Intro).

Añade dos cosas que aquellos visores no tenían:

* **Modo oscuro** (Ver ▸ Modo oscuro, `Ctrl+Mayús+D`), además de un modo «según el sistema».
* **Guardar en JPEG optimizado con MozJPEG** (`Ctrl+Mayús+S` en el visor, o en Convertir formato).
  Genera archivos JPEG normales, compatibles con cualquier programa, entre un 10 % y un 20 % más
  pequeños con la misma calidad. También se pueden recomprimir JPEG existentes **sin pérdida**
  (Herramientas ▸ Optimizar JPEG con MozJPEG).

## Instalación (AppImage)

1. Descargue `RetroVisor-x86_64.AppImage`.
2. Clic derecho ▸ Propiedades ▸ Permisos ▸ «Permitir ejecutar el archivo como un programa»
   (o `chmod +x RetroVisor-x86_64.AppImage`).
3. Doble clic para abrirlo.
4. Dentro del programa: **Herramientas ▸ Integrar en el menú de aplicaciones** y, si quiere que las
   fotos se abran siempre con él, **Herramientas ▸ Establecer como visor predeterminado de imágenes**.

No necesita FUSE ni `libfuse2`: la primera vez se descomprime en `~/.cache/retrovisor-appimage`
(tarda unos segundos) y las siguientes arranca al instante.

Funciona en Linux Mint 21.x y 22.x (y en otras distribuciones con glibc 2.35 o superior), 64 bits.

## Funciones

**Explorador**
* Árbol de carpetas y pestaña de Favoritos; panel de vista previa.
* Modos: Miniaturas, Iconos grandes, Iconos pequeños, Lista y Detalles (con columnas Tamaño, Tipo,
  Modificado, Propiedades de imagen y Descripción).
* Miniaturas en segundo plano con caché en disco; tamaño configurable (48–320 px).
* Ordenar por nombre, extensión, tamaño, fecha o dimensiones; filtro (todo / imágenes / patrón).
* Barra de dirección con historial, Atrás/Adelante/Subir, carpetas recientes.
* Cortar/Copiar/Pegar archivos (compatible con Nemo), arrastrar y soltar, Copiar/Mover a carpeta
  con carpetas recientes, Renombrar (F2), Renombrar en serie (plantilla `Foto###`), Eliminar a la
  papelera, Nueva carpeta.
* Descripciones de archivo guardadas en `descript.ion` (el mismo formato que usaban los visores de
  Windows, así se conservan las descripciones que ya tenía).
* Herramientas: Convertir formato (por lotes), Rotar/Voltear (JPEG **sin pérdida**), Redimensionar
  por lotes, Optimizar con MozJPEG, Fondo de escritorio (centrado, mosaico, ajustado, estirado,
  restaurar), Editar con programa externo (GIMP, Pinta…), Abrir terminal aquí, Adquirir desde
  escáner (Simple Scan), Imprimir con vista previa.

**Visor**
* Navegación con Av Pág/Re Pág, Espacio/Retroceso, Inicio/Fin, rueda del ratón, flechas.
* Zoom (+, −, tamaño real, ajustar, bloqueo de zoom), arrastrar para desplazar, pantalla completa.
* Presentación automática (retardo, repetir, aleatorio, inverso).
* GIF/WebP animados.
* Rotar, voltear, redimensionar, recortar a la selección (Mayús+arrastrar), ajustes de brillo,
  contraste, gamma, saturación y nitidez, autocontraste, escala de grises, negativo, enfocar,
  desenfocar; deshacer.
* Copiar/pegar imagen, Guardar, Guardar como (JPEG, **MozJPEG**, PNG, BMP, GIF, TIFF, PCX, TGA,
  WebP, PPM, ICO), Imprimir, Propiedades con datos EXIF, Fondo de escritorio.
* Precarga de la imagen siguiente para pasar de foto en foto al instante.

**Formatos de lectura**: JPEG, PNG, GIF, BMP, TIFF, PCX, TGA, ICO/CUR, PPM/PGM/PBM, WebP, AVIF,
HEIC/HEIF (fotos de iPhone), PSD, JPEG 2000, SVG, XBM/XPM, SGI, Sun Raster, DDS, ICNS, QOI…

`F1` muestra todos los atajos de teclado.

## Construir el AppImage

```bash
sudo apt install python3-venv python3-dev build-essential cmake nasm squashfs-tools \
     libsquashfuse-dev zlib1g-dev libzstd-dev liblzma-dev liblz4-dev liblzo2-dev libxcb-cursor0 \
     libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0
./build-appimage.sh
# Resultado: dist/RetroVisor-x86_64.AppImage
```

El script crea un entorno virtual de Python, compila `cjpeg` y `jpegtran` de MozJPEG como binarios
estáticos, congela la aplicación con PyInstaller y genera el AppImage con un *runtime* propio
(`appimage/runtime.c`) que no depende de FUSE.

## Ejecutar desde el código

```bash
pip install -r requirements.txt
python3 -m retrovisor [imagen o carpeta]
```

## Estructura

| Ruta | Contenido |
|---|---|
| `retrovisor/browser.py` | Ventana del explorador |
| `retrovisor/viewer.py` | Ventana del visor |
| `retrovisor/dialogs.py` | Diálogos (opciones, convertir, MozJPEG, propiedades, imprimir…) |
| `retrovisor/imaging.py` | Carga y guardado de imágenes, codificación MozJPEG |
| `retrovisor/fileops.py` | Papelera, portapapeles, `descript.ion`, fondo de escritorio |
| `retrovisor/thumbs.py` | Miniaturas en segundo plano y caché |
| `retrovisor/theme.py` | Estilo clásico, modo oscuro e iconos |
| `appimage/runtime.c` | Runtime del AppImage (sin FUSE) |
| `build-appimage.sh` | Script de construcción |

Configuración en `~/.config/retrovisor/retrovisor.ini`; caché de miniaturas en
`~/.cache/retrovisor/`.

## Licencias

Código propio de este proyecto. Usa Qt/PySide6 (LGPLv3), Pillow (MIT-CMU), pillow-heif
(BSD-3/LGPL) y MozJPEG (BSD/IJG). No contiene código, iconos ni recursos de ningún programa
comercial.
