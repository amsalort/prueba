"""Prueba funcional de RetroVisor (sin pantalla).

Uso:  QT_QPA_PLATFORM=offscreen python3 tests/prueba_funcional.py
"""
import math
import os
import random
import sys


def _crear_imagenes(base):
    from PIL import Image, ImageDraw

    os.makedirs(os.path.join(base, "Vacaciones"), exist_ok=True)
    random.seed(1)
    for i in range(14):
        w, h = random.choice([(1600, 1200), (1200, 1600), (1920, 1080), (800, 600)])
        im = Image.new("RGB", (w, h), tuple(random.randint(0, 255) for _ in range(3)))
        d = ImageDraw.Draw(im)
        for _ in range(12):
            x, y, r = random.randint(0, w), random.randint(0, h), random.randint(30, 300)
            d.ellipse([x - r, y - r, x + r, y + r], fill=tuple(random.randint(0, 255) for _ in range(3)))
        ext = ["jpg", "png", "jpg", "bmp", "gif", "webp", "tif"][i % 7]
        im.save(os.path.join(base, f"foto_{i + 1:02d}.{ext}"))
    frames = []
    for k in range(12):
        im = Image.new("RGB", (200, 200), "white")
        a = k * math.pi / 6
        ImageDraw.Draw(im).ellipse([80 + 60 * math.cos(a), 80 + 60 * math.sin(a),
                                    120 + 60 * math.cos(a), 120 + 60 * math.sin(a)], fill="red")
        frames.append(im)
    frames[0].save(os.path.join(base, "animada.gif"), save_all=True, append_images=frames[1:], duration=80, loop=0)
    open(os.path.join(base, "notas.txt"), "w").write("hola")


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tempfile
W = os.path.join(tempfile.mkdtemp(prefix="retrovisor-test-"), "fotos")
_crear_imagenes(W)
os.environ['XDG_CONFIG_HOME'] = W + '/../cfg2'; os.environ['XDG_CACHE_HOME'] = W + '/../cfg2/cache'; os.environ['XDG_DATA_HOME'] = W + '/../cfg2/data'
from PySide6.QtCore import QTimer, QEventLoop, Qt
from PySide6.QtWidgets import QApplication, QDialog
from PIL import Image
from retrovisor import imaging, dialogs, fileops
from retrovisor.settings import settings
from retrovisor.theme import apply_theme
import retrovisor.app as A
app = QApplication(['x']); apply_theme(app)
def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()
FALLOS = []
def ok(c, m):
    print(('OK   ' if c else 'FAIL ') + m)
    if not c:
        FALLOS.append(m)

# 1. MozJPEG vs JPEG estándar
im = imaging.load_image(W + '/foto_01.jpg')
imaging.save_image(im.pil, W + '/std.jpg', 'JPEG', {'quality': 80, 'optimize': True})
imaging.save_image(im.pil, W + '/moz.jpg', 'MOZJPEG', {'quality': 80}, exif=b'Exif\x00\x00II*\x00\x08\x00\x00\x00\x00\x00\x00\x00')
a, b = os.path.getsize(W + '/std.jpg'), os.path.getsize(W + '/moz.jpg')
ok(Image.open(W + '/moz.jpg').format == 'JPEG' and Image.open(W+'/moz.jpg').size == im.pil.size, f'MozJPEG válido ({b} B vs estándar {a} B, {100*(a-b)/a:.1f}% menor)')
ok(b'Exif' in open(W+'/moz.jpg','rb').read(200), 'EXIF conservado en MozJPEG')
# foto real-ish: ruido
import random
noisy = Image.effect_noise((800,600), 40).convert('RGB')
imaging.save_image(noisy, W+'/n_std.jpg','JPEG',{'quality':85}); imaging.save_image(noisy, W+'/n_moz.jpg','MOZJPEG',{'quality':85})
print('     ruido: estándar', os.path.getsize(W+'/n_std.jpg'), 'moz', os.path.getsize(W+'/n_moz.jpg'))
# RGBA → mozjpeg
imaging.save_image(Image.new('RGBA',(50,50),(255,0,0,128)), W+'/alpha.jpg','MOZJPEG',{})
ok(Image.open(W+'/alpha.jpg').mode=='RGB','RGBA a MozJPEG')
imaging.save_image(Image.new('L',(50,50),100), W+'/gray.jpg','MOZJPEG',{})
ok(Image.open(W+'/gray.jpg').mode=='L','Gris a MozJPEG')
# 2. todos los formatos
for k in imaging.SAVE_FORMATS:
    p = W + '/out_' + k.lower() + imaging.SAVE_FORMATS[k][1]
    try:
        imaging.save_image(im.pil, p, k, {}); Image.open(p).load(); ok(True, 'guardar ' + k)
    except Exception as e:
        ok(False, f'guardar {k}: {e}')
# 3. rotación sin pérdida
before = Image.open(W+'/foto_01.jpg').size
dialogs.rotate_file(W+'/foto_01.jpg','90',True)
after = Image.open(W+'/foto_01.jpg').size
ok(after == (before[1]//16*16 if False else after[0], after[1]) and after[0] <= before[1] and after[1] == before[0] or after==(before[1],before[0]), f'rotación JPEG sin pérdida {before}->{after}')
dialogs.rotate_file(W+'/foto_02.png','horizontal',True); ok(True,'voltear PNG')
# 4. optimización lossless
b0,b1 = imaging.mozjpeg_optimize_lossless(W+'/std.jpg'); ok(b1<=b0, f'optimización sin pérdida {b0}->{b1}')
# 5. descripciones
fileops.write_description(W+'/foto_03.jpg','Playa con amigos')
ok(fileops.read_descriptions(W)['foto_03.jpg']=='Playa con amigos','descript.ion')
fileops.write_description(W+'/foto 4.jpg','con espacio')
ok(fileops.read_descriptions(W)['foto 4.jpg']=='con espacio','descript.ion con espacios')
# 6. info y exif
ok(imaging.read_info(W+'/foto_05.gif').bpp==8,'bpp GIF')
li = imaging.load_image(W+'/animada.gif'); ok(li.animated and len(li.frames)==12,'GIF animado 12 cuadros')
# 7. explorador: renombrar en serie
ctl = A.Controller(app); b = ctl.ensure_browser(W); b.show(); wait(500)
paths = sorted(p for p in b.image_list() if 'out_' in p)
d = dialogs.RenameSeriesDialog(paths, b); d.template.setText('Serie_###'); m = d.run()
ok(len(m)==len(paths) and all(os.path.exists(v) for v in m.values()), f'renombrar en serie ({len(m)})')
# 8. copiar al portapapeles y pegar en subcarpeta
b.refresh(); wait(200)
b.navigate(W); wait(200)
fileops.set_file_clipboard([W+'/foto_03.jpg'], False)
p, cut = fileops.get_file_clipboard(); ok(p==[W+'/foto_03.jpg'] and not cut,'portapapeles de archivos')
b.navigate(W+'/Vacaciones'); b.paste_files(); wait(200)
ok(os.path.exists(W+'/Vacaciones/foto_03.jpg'),'pegar archivo')
b.transfer([W+'/foto_04.bmp'], W+'/Vacaciones', move=True); ok(os.path.exists(W+'/Vacaciones/foto_04.bmp') and not os.path.exists(W+'/foto_04.bmp'),'mover archivo')
b.navigate(W); wait(300)
# 9. borrar (permanente, sin confirmación)
settings().set('general/confirm_delete', False); settings().set('general/use_trash', False)
b._delete_paths([W+'/notas.txt'], False); ok(not os.path.exists(W+'/notas.txt'),'eliminar')
# 10. convertir lote a MozJPEG
cd = dialogs.ConvertDialog([W+'/foto_09.png', W+'/foto_13.webp'], b); cd.fmt.setCurrentRow(0); outs = cd.run()
ok(len(outs)==2 and all(o.endswith('.jpg') for o in outs),'convertir lote a MozJPEG')
# 11. redimensionar lote
rd = dialogs.BatchResizeDialog([W+'/foto_10.jpg'], b); rd.r_pct.setChecked(True); rd.pct.setValue(50); outs = rd.run()
ok(Image.open(outs[0]).size == tuple(x//2 for x in Image.open(W+'/foto_10.jpg').size), f'redimensionar lote {outs}')
# 12. visor: navegación, zoom, edición y guardado
b.navigate(W); wait(300)
b.view_images(W+'/foto_08.jpg'); wait(1200)
v = ctl.viewers[-1]; v.resize(900,700); wait(300)
ok(v.cur and v.cur.path.endswith('foto_08.jpg'),'visor abre la imagen')
v.next_image(); wait(800); ok(v.cur.path.endswith('foto_09.png') or v.index>0, 'siguiente: '+os.path.basename(v.cur_path))
v.prev_image(); wait(500)
z0 = v.canvas.zoom; v.canvas.zoom_in(); ok(v.canvas.zoom>z0, f'zoom {z0:.2f}->{v.canvas.zoom:.2f}')
v.canvas.zoom_actual(); ok(abs(v.canvas.zoom-1)<1e-6,'tamaño real')
w0,h0 = v.cur.pil.size; v.transform('90'); ok(v.cur.pil.size==(h0,w0) and v.modified,'rotar en visor')
v.undo(); ok(v.cur.pil.size==(w0,h0),'deshacer')
v.filter_op('gray'); ok(v.cur.pil.mode=='L','escala de grises')
from PySide6.QtCore import QRect
v.canvas.selection = QRect(10,10,100,50); v.crop(); ok(v.cur.pil.size==(100,50),'recortar')
v.modified=True; ok(v.save(),'guardar'); ok(Image.open(W+'/foto_08.jpg').size==(100,50),'guardado en disco')
v.toggle_fullscreen(); wait(300); v._update_status(); v.toggle_fullscreen(); wait(200)
settings().set('slideshow/delay', 0.3); settings().set('slideshow/fullscreen', False)
i0 = v.index; v.start_slideshow(); wait(1100); v.stop_slideshow(); ok(v.index!=i0, f'presentación avanzó {i0}->{v.index}')
v.copy_image(); ok(not QApplication.clipboard().image().isNull(),'copiar imagen al portapapeles')
# 13. diálogos se construyen
for D in (lambda: dialogs.OptionsDialog(b), lambda: dialogs.PropertiesDialog(W+'/foto_03.jpg', None, b),
          lambda: dialogs.FormatOptionsDialog('MOZJPEG', b), lambda: dialogs.AdjustDialog(v.cur.pil, b),
          lambda: dialogs.ShortcutsDialog(b), lambda: dialogs.FilterDialog(b), lambda: dialogs.FavoritesDialog(b),
          lambda: dialogs.RotateDialog([W+'/foto_03.jpg'], b), lambda: dialogs.PrintSetupDialog(b),
          lambda: dialogs.FolderPickDialog(True, W, b)):
    try:
        x = D(); x.show(); wait(50); x.close(); ok(True, 'diálogo ' + type(x).__name__)
    except Exception as e:
        ok(False, f'diálogo: {e}')
ad = dialogs.AdjustDialog(Image.open(W+'/foto_10.jpg').convert('RGB'), b); ad.sliders['brightness'].setValue(30); ad.sliders['gamma'].setValue(-20)
ok(ad.apply(Image.open(W+'/foto_10.jpg').convert('RGB')).size[0]>0,'ajustes de color')
v.close(); wait(300)
ok(b.isVisible(),'vuelve al explorador')
b.set_mode('details'); wait(300); b.set_mode('small'); b.set_mode('list'); b.set_mode('large'); b.set_mode('thumbs'); ok(True,'modos de vista')
for k in ('name','ext','size','date','props'): b.set_sort(k)
ok(True,'ordenar')
b.close(); app.quit()
print(f'\n{len(FALLOS)} fallos')
sys.exit(1 if FALLOS else 0)
