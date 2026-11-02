import cv2, numpy as np
I = r'C:/Users/zeze/AppData/Local/Temp/claude/D--BX----2026----------pikmin/a7e2d1e1-a3d2-474c-93fa-d61a703782af/images/'
REF_W = 1019
def ref(p):
    im = cv2.imread(I + p); k = REF_W / im.shape[1]
    return cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR if k > 1 else cv2.INTER_AREA)
icon = ref('13.webp')[1371:1397, 371:398].copy()
cv2.imwrite('work/icon.png', icon)

def badges(img, th=0.75):
    res = cv2.matchTemplate(img, icon, cv2.TM_CCOEFF_NORMED)
    pts = []
    for y, x in sorted(zip(*np.where(res >= th)), key=lambda p: -res[p]):
        if all(abs(x - a) > 20 or abs(y - b) > 20 for a, b in pts): pts.append((x, y))
    return pts

def digit_glyphs(img, x, y):
    reg = img[y - 6:y + 30, x + 27:x + 70]
    wm = (reg.min(axis=2) > 170).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(wm)
    gl = []
    for i in range(1, n):
        gx, gy, gw, gh, a = st[i]
        if gh >= 14 and a >= 15:  # 숫자 높이
            gl.append((gx, (lab[gy:gy + gh, gx:gx + gw] == i).astype(np.uint8)))
    return [g for _, g in sorted(gl, key=lambda t: t[0])]

out = []
for p in ['13.webp', '6.webp', '3.webp', '4.webp', '5.webp', '9.webp']:
    im = ref(p)
    for (x, y) in badges(im):
        gs = digit_glyphs(im, x, y)
        print(p, (x, y), [g.shape for g in gs])
        for j, g in enumerate(gs):
            out.append(cv2.resize(g * 255, (24, 36), interpolation=cv2.INTER_NEAREST))
if out: cv2.imwrite('work/glyphs.png', np.hstack([np.pad(o, 2, constant_values=128) for o in out]))

from PIL import Image, ImageDraw, ImageFont
real = []
for p, lab in [('13.webp', None), ('6.webp', None), ('5.webp', None)]:
    im = ref(p)
    for (x, y) in badges(im):
        for g in digit_glyphs(im, x, y):
            if g.shape[0] >= 17: real.append(g)
labels = ['6', '5', '5', '3', '7', '7']
assert len(real) == 6, len(real)
def N(g): return cv2.resize(g.astype(np.float32), (12, 18), interpolation=cv2.INTER_AREA).ravel()
def render(font, d):
    f = ImageFont.truetype(font, 80); im = Image.new('L', (100, 120), 0)
    ImageDraw.Draw(im).text((10, 0), d, fill=255, font=f)
    a = (np.array(im) > 128).astype(np.uint8); ys, xs = np.where(a)
    return a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
for font in ['GOTHIC.TTF', 'arial.ttf', 'calibri.ttf', 'corbel.ttf', 'Candara.ttf', 'bahnschrift.ttf', 'segoeui.ttf', 'verdana.ttf', 'tahoma.ttf', 'trebuc.ttf']:
    try: T = {d: N(render('C:/Windows/Fonts/' + font, d)) for d in '0123456789'}
    except Exception as e: print(font, 'X'); continue
    pred = []
    for g in real:
        v = N(g); sc = {d: np.corrcoef(v, t)[0, 1] for d, t in T.items()}
        pred.append(max(sc, key=sc.get))
    print(font, ''.join(pred), sum(p == l for p, l in zip(pred, labels)))

import os
os.makedirs('templates/digits', exist_ok=True)
G = 'C:/Windows/Fonts/GOTHIC.TTF'
for d in '0123456789':
    samples = [g for g, l in zip(real, labels) if l == d]
    g = samples[0] if samples else render(G, d)
    cv2.imwrite(f'templates/digits/{d}.png', g * 255)
I2 = I
b = ref('10.webp'); cv2.imwrite('templates/ui_move_here.png', b[1862:1938, 672:948])
c = ref('11.webp'); cv2.imwrite('templates/ui_open_map.png', c[1688:1748, 380:640])
print('saved')
