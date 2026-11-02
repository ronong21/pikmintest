import cv2, numpy as np
I = r'C:/Users/zeze/AppData/Local/Temp/claude/D--BX----2026----------pikmin/a7e2d1e1-a3d2-474c-93fa-d61a703782af/images/'
REF_W = 1019
def ref(p):
    im = cv2.imread(I + p); k = REF_W / im.shape[1]
    return cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_LINEAR if k > 1 else cv2.INTER_AREA)

def find_badges(img):
    b, g, r = [img[..., i].astype(int) for i in range(3)]
    m = ((abs(b - 100) < 28) & (abs(g - 126) < 25) & (r < 100)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if 18 <= h <= 60 and 1.4 < w / h < 4.5 and a > 0.55 * w * h:
            out.append((x, y, w, h))
    return out

def glyphs(img, box):
    x, y, w, h = box
    p = img[y:y + h, x:x + w]
    wm = (p.min(axis=2) > 165).astype(np.uint8)
    cols = wm.sum(0) > 0
    groups, s = [], None
    for i, c in enumerate(list(cols) + [False]):
        if c and s is None: s = i
        if not c and s is not None: groups.append((s, i)); s = None
    res = []
    for a, b2 in groups:
        g = wm[:, a:b2]; rows = np.where(g.sum(1) > 0)[0]
        res.append(g[rows[0]:rows[-1] + 1])
    return res  # [icon, digit, digit...]

def norm(g):
    return cv2.resize(g.astype(np.float32), (12, 18), interpolation=cv2.INTER_AREA)

for p in ['13.webp', '6.webp', '3.webp', '4.webp']:
    im = ref(p)
    for bx in find_badges(im):
        gs = glyphs(im, bx)
        print(p, bx, [g.shape for g in gs])
