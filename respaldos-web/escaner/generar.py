"""Regenera los recursos gráficos del escáner a partir de los planos.

Uso (desde la carpeta Web ApexQuant):
    python escaner/generar.py --iso planos/isometrica.png --despiece planos/explotada.png
    python escaner/generar.py --iso hoja.png --caja-iso 275,108,689,548     (recortar el dibujo de una hoja con rótulo)
    python escaner/generar.py --solo-facetas                                 (recalcular solo la vista general)

Qué hace:
  1. Convierte cada plano (líneas oscuras sobre fondo blanco) en dos imágenes con fondo transparente:
     líneas blancas y líneas turquesa con brillo. Las guarda en img/ con los nombres que usa escaner.js.
  2. Saca la silueta de la isométrica y la divide en tantas facetas como diapositivas tenga la
     presentación (Voronoi centroidal). Cada diapositiva de la vista general toma la forma de una faceta.
     Escribe escaner/facetas.js.

Necesita Python 3 con Pillow, numpy y matplotlib (contourpy):  pip install pillow numpy matplotlib
"""
import argparse, json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(RAIZ, 'img')
BLANCO, TURQ, NUCLEO = (232, 244, 246), (95, 227, 211), (200, 250, 244)


def lineas(origen, caja, nombre, escala=2):
    """Plano (negro sobre blanco) -> líneas blancas y turquesa con brillo, fondo transparente."""
    g = Image.open(origen).convert('RGBA')
    fondo = Image.new('RGBA', g.size, (255, 255, 255, 255)); fondo.alpha_composite(g)
    g = fondo.convert('L')
    if caja:
        g = g.crop(caja)
    g = g.resize((g.width * escala, g.height * escala), Image.LANCZOS)
    a = np.clip((255 - np.array(g).astype(np.float32)) * 1.7, 0, 255); a[a < 18] = 0
    alfa = Image.fromarray(a.astype(np.uint8))
    def guardar(rgb, alfa_np, sufijo):
        im = np.zeros((g.height, g.width, 4), np.uint8); im[..., :3] = rgb; im[..., 3] = alfa_np
        ruta = os.path.join(IMG, f'{nombre}-{sufijo}.webp')
        Image.fromarray(im, 'RGBA').save(ruta, 'WEBP', quality=90, alpha_quality=95, method=6)
        print('  ', os.path.relpath(ruta, RAIZ), g.size)
    guardar(BLANCO, a.astype(np.uint8), 'blanco')
    brillo = np.array(alfa.filter(ImageFilter.GaussianBlur(7))).astype(np.float32)
    t = (a / 255.0)[..., None]
    rgb = (np.array(TURQ, np.float32) * (1 - t) + np.array(NUCLEO, np.float32) * t).astype(np.uint8)
    guardar(rgb, np.clip(np.maximum(a, brillo * 1.5), 0, 255).astype(np.uint8), 'turquesa')


def chaikin(p, it=2, c=.12):
    for _ in range(it):
        q = []
        for i in range(len(p)):
            a0, a1 = p[i], p[(i + 1) % len(p)]
            q += [a0 * (1 - c) + a1 * c, a0 * c + a1 * (1 - c)]
        p = np.array(q)
    return p


def simplificar(p, tol):
    def dp(pts):
        if len(pts) < 3:
            return pts
        a0, a1 = pts[0], pts[-1]; v = a1 - a0; n = np.hypot(*v) or 1; w = pts - a0
        dist = np.abs(v[0] * w[:, 1] - v[1] * w[:, 0]) / n; i = dist.argmax()
        return np.vstack([dp(pts[:i + 1])[:-1], dp(pts[i:])]) if dist[i] > tol else np.array([a0, a1])
    p = p[:-1] if np.allclose(p[0], p[-1]) else p
    j = int(((p - p[0]) ** 2).sum(1).argmax())
    return np.vstack([dp(p[:j + 1])[:-1], dp(np.vstack([p[j:], p[:1]]))[:-1]])


def contorno(mascara):
    from contourpy import contour_generator
    return max(contour_generator(z=np.pad(mascara.astype(float), 1)).lines(0.5), key=len) - 1


def facetas(n):
    """Silueta de la isométrica dividida en n facetas parejas (Voronoi centroidal)."""
    a = Image.open(os.path.join(IMG, 'aq1-iso-blanco.webp')).split()[3]
    a = a.resize((a.width // 2, a.height // 2), Image.LANCZOS)
    b = a.point(lambda v: 255 if v > 40 else 0).filter(ImageFilter.MaxFilter(5))
    ext = b.copy(); ImageDraw.floodfill(ext, (0, 0), 128)
    sil = np.array(Image.fromarray(((np.array(ext) != 128) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))) > 0
    H, W = sil.shape
    ys, xs = np.nonzero(sil); P = np.stack([xs, ys], 1).astype(float)
    C = P[np.random.default_rng(7).choice(len(P), n, replace=False)]
    for _ in range(80):
        lab = ((P[:, None, :] - C[None]) ** 2).sum(2).argmin(1)
        C = np.array([P[lab == k].mean(0) for k in range(n)])
    filas = np.round(C[:, 1] / (H / 5)).astype(int)
    orden = sorted(range(n), key=lambda k: (filas[k], C[k, 0]))
    L = np.full((H, W), -1); L[ys, xs] = lab
    piezas, centros = [], []
    for k in orden:
        m = np.array(Image.fromarray(((L == k) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))) > 0
        piezas.append(np.round(chaikin(simplificar(contorno(m), 1.5), 2), 1).tolist())
        centros.append(np.round(C[k], 1).tolist())
    silueta = np.round(chaikin(simplificar(contorno(sil), 1.0), 1), 1).tolist()
    datos = {'w': W, 'h': H, 'p': piezas, 'c': centros, 's': silueta}
    ruta = os.path.join(RAIZ, 'escaner', 'facetas.js')
    with open(ruta, 'w', encoding='utf-8') as f:
        f.write('/* Generado por escaner/generar.py: silueta de la isométrica dividida en facetas\n'
                '   (una por diapositiva) para la vista general de la presentación. No editar a mano. */\n')
        f.write('window.ESCANER_FACETAS = ' + json.dumps(datos, separators=(',', ':')) + ';\n')
    print('  ', os.path.relpath(ruta, RAIZ), f'{n} facetas')


def caja(texto):
    return tuple(int(v) for v in texto.split(',')) if texto else None


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--iso', help='plano de la isométrica (líneas oscuras sobre blanco)')
    ap.add_argument('--caja-iso', help='recorte x0,y0,x1,y1 del dibujo dentro de la hoja')
    ap.add_argument('--despiece', help='plano de la isométrica explotada')
    ap.add_argument('--caja-despiece', help='recorte x0,y0,x1,y1 del dibujo dentro de la hoja')
    ap.add_argument('--facetas', type=int, default=15, help='cantidad de diapositivas (15)')
    ap.add_argument('--solo-facetas', action='store_true', help='no tocar las imágenes, solo recalcular las facetas')
    args = ap.parse_args()
    if not args.solo_facetas:
        if args.iso:
            print('isométrica:'); lineas(args.iso, caja(args.caja_iso), 'aq1-iso')
        if args.despiece:
            print('explotada:'); lineas(args.despiece, caja(args.caja_despiece), 'despiece')
    print('vista general:'); facetas(args.facetas)
    print('Listo. Revisá las posiciones de escaner.js con ApexQuant.html#calibrar')
