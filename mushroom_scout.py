"""피크민 블룸 버섯 탐지기.

adb(PC) 또는 Shizuku rish(폰 Termux)로 화면을 캡처하고, templates/ 의 버섯 이미지와 비교해 찾는다.
scan 모드는 지도를 드래그로 돌려 가며 훑고, --want 버섯을 찾으면 인원 배지를 읽어
max-people 이하일 때 버섯 → 이름표 → 여기로 이동 → 지도 앱 열기 를 눌러 좌표를 얻는다.

  python mushroom_scout.py grab                          # 현재 화면 저장
  python mushroom_scout.py scan --want fancy_giant       # 거대 화려한 버섯(4명 이하) 좌표 찾기
  python mushroom_scout.py selftest                      # 탐지 로직·템플릿 자체 점검
"""
import argparse, glob, itertools, json, os, random, re, shlex, shutil, signal, subprocess, sys, threading, time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
_PC_ADB = os.path.join(HERE, "tools", "platform-tools", "adb.exe")
_RISH = os.path.expanduser("~/bin/rish")  # 폰(Termux)에서는 Shizuku 의 rish 로 shell 권한을 얻는다
ADB = os.environ.get("ADB") or next((p for p in (_PC_ADB, _RISH) if os.path.exists(p)), "adb")
SERIAL = os.environ.get("ADB_SERIAL")  # 폰 여러 대일 때만 지정
CAP_PATH = "/storage/emulated/0/Download/.scout_cap.png"  # rish(shell)가 쓰고 Termux 가 읽을 수 있는 공용 위치
UI_PATH = "/storage/emulated/0/Download/.scout_ui.xml"    # 지도 앱 화면 글자(uiautomator dump)
PIKMIN_PKG = "com.nianticlabs.pikmin"
PIKMIN_ACT = "com.nianticproject.ichigo.IchigoUnityPlayerActivity"  # APK 의 시작 화면
MAPS_PKG = "com.google.android.apps.maps"
# 백그라운드 모드: 폰 화면 밖 가상 화면(scout_display.dex)에서 피크민을 돌린다
VD = None  # 가상 화면 번호 (None = 폰 화면 그대로 쓰는 기본 모드)
VD_DIR = "/storage/emulated/0/Download/.scout"
VD_DEX = "/data/local/tmp/scout_display.dex"
VD_SCALE = 1.0  # 가상 화면 크기 배율 (--vd-scale). 낮추면 발열↓, 너무 낮추면 인원 숫자를 잘못 읽을 수 있음
STOPPED = threading.Event()  # 엔터로 중지
VD_LOG = "/data/local/tmp/scout_vd.log"  # 도우미 출력 (셸 쪽 파일이라 cat 으로 읽는다)
OV_LOG = "/data/local/tmp/scout_ov.log"  # 떠 있는 [중지] 버튼 출력 (누르면 STOP)
OVERLAY_ON = False
QUERY_RE = re.compile(r"query=(-?\d{1,2}\.\d{4,})(?:,|%2C)\s*(-?\d{1,3}\.\d{4,})")  # 피크민이 지도 앱에 넘긴 주소
COORD_RE = re.compile(r"(-?\d{1,2}\.\d{4,}),\s*(-?\d{1,3}\.\d{4,})")  # 구글 지도 검색창의 "위도,경도"
THRESHOLD = 0.75          # 매칭 점수 하한. 오탐 많으면 올리고, 놓치면 내린다
SCALES = (0.85, 1.0, 1.15)  # 줌 차이 흡수
FAST = 0.5  # 비교할 때 화면을 이 배율로 줄인다(속도). 놓치면 0.7~1.0 으로 올린다
REF_W = 1019  # 템플릿을 자른 스크린샷의 가로 픽셀. 폰 해상도가 달라도 이 폭으로 맞춰 비교한다
# 광역 지도에서 드래그 한 번에 옮기는 거리(화면 가로, 세로 비율). 천천히 끌어야 관성으로 더 밀리지 않는다
PAN = (0.55, 0.30)
PAN_MS = 700
TAP_MS = 100  # 버섯 누르는 시간(ms). 블룸은 짧게 눌렀다 떼야 이름표가 뜬다 (너무 짧으면 게임이 못 봄)
SEA_LIMIT = 0.6  # 화면의 이 비율 이상이 바다면 그 칸 너머로는 안 나간다 (그 칸 안의 버섯은 찾음). 해안가 마을도 보게 넉넉히
STILL_DIFF = 0.6  # 드래그 뒤 화면 변화가 이보다 작으면 "안 움직임"(팝업 등). 3번 연속이면 멈추고 알림
MOVES = {"동": (1, 0), "서": (-1, 0), "남": (0, 1), "북": (0, -1)}
NAMES = {"fancy_giant": "거대 화려한 버섯", "fancy_mid": "중형 화려한 버섯"}  # --want 값 → 알림/내역 이름
WANT_NAME = "화려한 버섯"
MY_DIR = os.path.join(HERE, "my_targets")  # 앱 화면에서 등록한 버섯 사진(키.jpg) + names.json {키: 이름}


def all_targets():
    """찾을 수 있는 버섯 {키: 이름} = 기본(이번 달) + 직접 등록한 것."""
    t = dict(NAMES)
    try:
        t.update(json.load(open(os.path.join(MY_DIR, "names.json"), encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return t
FOUND = os.path.join(HERE, "found.txt")
AUDIT = False  # 후보 검수 모드 (--audit): 조건에 안 맞는 후보도 사진+좌표 저장 (알림 없음). 거대 버섯을 잘 거르는지 확인용
AUDIT_MAX = 30
AUDIT_DIR = os.path.join(HERE, "audit")  # 찾은 버섯 내역 (홈 화면 [버섯내역] 버튼으로 본다)


def _cmd(*args):
    if VD is not None and args[1:2] == ("input",):  # 터치/키 입력은 가상 화면으로
        args = args[:2] + ("-d", str(VD)) + args[2:]
    if os.path.basename(ADB).startswith("rish"):  # "exec-out"/"shell" 은 빼고 rish 에 명령만 넘긴다
        return [ADB, "-c", " ".join(args[1:])]
    return [ADB] + (["-s", SERIAL] if SERIAL else []) + list(args)


def adb(*args, binary=False, check=True, timeout=40):
    try:
        r = subprocess.run(_cmd(*args), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:  # Shizuku 쪽이 응답 없음: 멈춰 있지 말고 알린다
        if check:
            sys.exit(f"응답 없음({timeout}초): {' '.join(args[1:])[:120]}\nShizuku 앱이 '실행 중'인지 확인하세요.")
        print(f"   (응답 없음, 건너뜀: {' '.join(args[1:])[:60]})")
        return b"" if binary else ""
    if r.returncode and check:
        sys.exit(f"adb 실패: {r.stderr.decode(errors='ignore').strip()}\n"
                 "폰은 Shizuku 앱이 '실행 중'인지 확인하세요(재부팅하면 다시 시작해야 함). PC 는 USB 디버깅/ADB_SERIAL 확인.")
    return r.stdout if binary else r.stdout.decode(errors="ignore")


def screencap():
    if VD is not None:  # 가상 화면 도우미에게 요청하고 새 그림이 저장될 때까지 기다린다
        cap = VD_DIR + "/cap.jpg"
        old = os.path.getmtime(cap) if os.path.exists(cap) else 0
        open(VD_DIR + "/req", "w").close()
        for _ in range(100):
            time.sleep(0.05)
            if os.path.exists(cap) and os.path.getmtime(cap) != old:
                img = cv2.imread(cap)
                if img is not None:
                    return img
        sys.exit("가상 화면 캡처 실패: Shizuku 가 켜져 있는지, ~/scout/vd.log 를 확인하세요.")
    if os.path.basename(ADB).startswith("rish"):  # rish 는 바이너리 출력을 깨뜨려서 파일로 받는다
        adb("shell", "screencap", "-p", CAP_PATH)
        img = cv2.imread(CAP_PATH)
    else:
        png = adb("exec-out", "screencap", "-p", binary=True)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        sys.exit("화면 캡처 실패: adb 연결과 USB 디버깅(블루스택은 설정>고급>ADB)을 확인하세요.")
    return img


def pan(shape, d):
    """광역 지도를 d(동서남북) 쪽으로 한 칸 옮긴다. 손가락은 반대 방향으로 끈다."""
    stop_check()
    h, w = shape[:2]
    dx, dy = MOVES[d]
    j = lambda: random.uniform(-0.03, 0.03)  # 매번 같은 궤적이 되지 않게 흔든다
    cx, cy = (0.5 + j()) * w, (0.45 + j()) * h  # 위아래 게임 버튼을 피한 가운데
    pts = [cx + dx * PAN[0] * w / 2, cy + dy * PAN[1] * h / 2, cx - dx * PAN[0] * w / 2, cy - dy * PAN[1] * h / 2]
    adb("shell", "input", "swipe", *(str(int(p)) for p in pts), str(PAN_MS + random.randint(-100, 100)))


def drag(w, h, dx, dy):
    """지도를 (dx, dy) 픽셀만큼 끈다 (손가락이 그만큼 움직임). 천천히 끌어 관성으로 더 밀리지 않게."""
    sx, sy = w / 2 - dx / 2, 0.5 * h - dy / 2
    pts = [sx, sy, sx + dx, sy + dy]
    adb("shell", "input", "swipe", *(str(int(v)) for v in pts), str(PAN_MS + 200))


def sea_ratio(img):
    """화면(위아래 버튼 줄 제외) 중 파란 바다 비율."""
    h = img.shape[0]
    a = cv2.resize(img[int(h * 0.08):int(h * 0.88)], None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA).astype(int)
    b, g, r = a[..., 0], a[..., 1], a[..., 2]
    return float(((b > g + 15) & (b > r + 40)).mean())


def explore(max_cells=None):
    """시작 칸(0,0)에서 가까운 칸부터 바깥쪽으로 훑는 순서를 낸다 (max_cells 없으면 끝없이).
    바다 칸 너머로는 넓히지 않고, 본 칸은 다시 안 본다. 같은 거리면 지금 칸에서 가까운 칸부터(이동 줄이기).
    yield 한 칸에 대해 send(바다인지) 로 결과를 돌려받는다."""
    seen, frontier, last = set(), {(0, 0)}, (0, 0)
    while frontier and (max_cells is None or len(seen) < max_cells):
        cell = min(frontier, key=lambda c: (abs(c[0]) + abs(c[1]), abs(c[0] - last[0]) + abs(c[1] - last[1]), c))
        frontier.discard(cell)
        seen.add(cell)
        last = cell
        is_sea = yield cell
        if not is_sea:
            for dx, dy in MOVES.values():
                n = (cell[0] + dx, cell[1] + dy)
                if n not in seen:
                    frontier.add(n)


def travel(shape, src, dst):
    """src 칸에서 dst 칸까지 한 칸씩 드래그."""
    x, y = src
    while (x, y) != dst:
        d = ("동" if dst[0] > x else "서") if dst[0] != x else ("남" if dst[1] > y else "북")
        if VD is None:
            ensure_pikmin_front()  # 드래그 직전마다 확인
        pan(shape, d)
        x, y = x + MOVES[d][0], y + MOVES[d][1]
        time.sleep(random.uniform(0.6, 1.0))


def load_templates(sub="", flags=cv2.IMREAD_COLOR):
    """templates/<sub> 의 png/jpg 를 {이름: 이미지} 로. 버섯은 templates/, 버튼은 ui/, 배지 숫자는 digits/."""
    tpls = {}
    d = os.path.join(HERE, "templates", sub)
    for p in glob.glob(os.path.join(d, "*.png")) + glob.glob(os.path.join(d, "*.jpg")):
        t = cv2.imdecode(np.fromfile(p, np.uint8), flags)
        if t is not None:
            tpls[os.path.splitext(os.path.basename(p))[0]] = t
    return tpls


UI = load_templates("ui")
DIGITS = load_templates("digits", cv2.IMREAD_GRAYSCALE)


def to_ref(img):
    """템플릿은 가로 REF_W 화면에서 잘랐으므로 화면을 그 폭으로 맞춘다. (맞춘 화면, 배율) 반환."""
    k = REF_W / img.shape[1]
    if abs(k - 1) > 0.01:
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    return img, k


def detect(img, tpls, threshold=THRESHOLD):
    """[(이름, 점수, x, y)] 를 점수 높은 순으로. 같은 자리 중복은 가장 높은 것 하나만 남긴다.
    좌표는 원래 화면 기준."""
    img, k = to_ref(img)
    img = cv2.resize(img, None, fx=FAST, fy=FAST, interpolation=cv2.INTER_AREA)  # 폰 CPU 가 느려서 줄여서 비교
    k *= FAST
    cands = []
    for name, t in tpls.items():
        for s in SCALES:
            ts = cv2.resize(t, None, fx=s * FAST, fy=s * FAST, interpolation=cv2.INTER_AREA)
            th, tw = ts.shape[:2]
            if th > img.shape[0] or tw > img.shape[1]:
                continue
            res = cv2.matchTemplate(img, ts, cv2.TM_CCOEFF_NORMED)
            for y, x in zip(*np.where(res >= threshold)):
                cands.append((name, float(res[y, x]), x + tw // 2, y + th // 2, max(tw, th)))
    cands.sort(key=lambda c: -c[1])
    hits = []
    # ponytail: O(n²) 중복 제거. 후보가 수천 개로 늘면 격자 버킷으로 바꾼다
    for c in cands:
        if all((c[2] - h[2]) ** 2 + (c[3] - h[3]) ** 2 > (0.6 * h[4]) ** 2 for h in hits):
            hits.append(c)
    return [(n, sc, int(x / k), int(y / k)) for n, sc, x, y, _ in hits]


def _digit_vec(g):
    v = cv2.resize((g > 0).astype(np.float32), (12, 18), interpolation=cv2.INTER_AREA).ravel()
    return v - v.mean()


def read_people(img, x, y):
    """버섯(x, y) 위쪽 인원 배지(사람 아이콘 + 숫자)를 읽는다. 배지가 없으면 0, 숫자를 못 읽으면 None."""
    ref, k = to_ref(img)
    x, y = int(x * k), int(y * k)
    x0, y0 = max(0, x - 80), max(0, y - 150)
    roi = ref[y0:y + 20, x0:x + 140]
    icon = UI["badge_icon"]
    if roi.shape[0] < icon.shape[0] or roi.shape[1] < icon.shape[1]:
        return 0
    _, sc, _, (ix, iy) = cv2.minMaxLoc(cv2.matchTemplate(roi, icon, cv2.TM_CCOEFF_NORMED))
    if sc < 0.75:
        return 0
    ix, iy = ix + x0, iy + y0
    reg = ref[max(0, iy - 6):iy + 30, ix + 27:ix + 70]  # 아이콘 오른쪽 숫자 칸
    n, lab, st, _ = cv2.connectedComponentsWithStats((reg.min(axis=2) > 170).astype(np.uint8))
    glyphs = sorted((st[i, 0], lab[st[i, 1]:st[i, 1] + st[i, 3], st[i, 0]:st[i, 0] + st[i, 2]] == i)
                    for i in range(1, n) if st[i, 3] >= 17 and st[i, 4] >= 15)
    if not glyphs:
        return None
    s = ""
    for _, g in glyphs:
        v = _digit_vec(g)
        scores = {d: float(v @ _digit_vec(t) / (np.linalg.norm(v) * np.linalg.norm(_digit_vec(t)) + 1e-6))
                  for d, t in DIGITS.items()}
        best = max(scores, key=scores.get)
        if scores[best] < 0.5:
            return None
        s += best
    return int(s)


def tap(x, y, ms=None):
    # input tap 은 누르고 바로 떼서, 블룸(Unity)이 지도 위 버섯 터치로 못 알아본다 → 제자리에서 ms 동안 누르기
    x, y = str(int(x)), str(int(y))
    adb("shell", "input", "swipe", x, y, x, y, str(ms or TAP_MS))


def find_ui(img, name, threshold=0.8):
    hits = detect(img, {name: UI[name]}, threshold)
    return hits[0][2:] if hits else None


def find_label(img, x, y, before=None):
    """버섯을 누르면 뜨는 검은 이름표의 가운데. 없으면 None.
    before(누르기 전 화면)를 주면: 그때 없던 검은 덩어리가 새로 생겼으면 크기·위치(가장자리에 잘려도) 상관없이 이름표로 본다."""
    w = img.shape[1]
    dark = (img.max(axis=2) < 60).astype(np.uint8)
    old = (before.max(axis=2) < 60) if before is not None and before.shape == img.shape else None
    n, _, st, cen = cv2.connectedComponentsWithStats(dark)
    best = None
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        cx, cy = cen[i]
        if old is not None:  # 새로 생긴 검은 덩어리(누르기 전 자리엔 검은 게 거의 없던 곳)
            ok = bw > 0.08 * w and bh > 0.02 * w and old[by:by + bh, bx:bx + bw].mean() < 0.2
        else:  # 예전 방식: 버섯 위쪽 근처의 이름표 모양
            ok = bw > 0.18 * w and 0.02 * w < bh < 0.30 * w and y - 0.45 * w < cy < y + 0.05 * w and abs(cx - x) < 0.35 * w
        d = (cx - x) ** 2 + (cy - y) ** 2
        if ok and (best is None or d < best[2]):
            best = (int(cx), int(cy), d)
    return best[:2] if best else None


def map_queries():
    return set(QUERY_RE.findall(adb("shell", "dumpsys activity activities", check=False)))


def read_map_coords(tries=8, exclude=frozenset()):
    """피크민이 지도 앱에 넘긴 주소(query=위도,경도)를 읽는다. exclude(열기 전부터 있던 주소)는 빼고 새로 생긴 것만.
    안 되면 지도 앱 화면 글자를 덤프해 찾는다."""
    for _ in range(tries):
        time.sleep(1.5)
        new = map_queries() - exclude
        if new:
            return sorted(new)[0]
        if VD is not None:  # uiautomator 는 폰 화면만 읽는다
            continue
        adb("shell", "uiautomator", "dump", UI_PATH, check=False)
        try:
            m = COORD_RE.search(open(UI_PATH, encoding="utf-8", errors="ignore").read())
        except OSError:
            continue
        if m:
            return m.group(1), m.group(2)
    return None


def launch_pikmin():
    if VD is None:
        adb("shell", "monkey", "-p", PIKMIN_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    else:
        adb("shell", f"am start --display {VD} -n {PIKMIN_PKG}/{PIKMIN_ACT}", check=False)


ZOOM_OUT = True  # 광역 지도에 들어가면 최대로 줌아웃 (버섯 아이콘 크기는 그대로, 한 화면에 2~3배 넓게) --no-zoom 으로 끔
_zoomed = False


def pinch(shape, zoom_in=False):
    """두 손가락 오므리기(줌아웃) / 벌리기(줌인) 2번. 도우미가 터치를 직접 넣는다."""
    install_dex()
    h, w = shape[:2]
    a, b = w * 0.38, w * 0.05
    if zoom_in:
        a, b = b, a
    for _ in range(2):
        adb("shell", f"CLASSPATH={VD_DEX} app_process / ScoutDisplay pinch {VD or 0} {w / 2:.0f} {h * 0.5:.0f} "
                     f"{a:.0f} {b:.0f}", check=False, timeout=20)
        time.sleep(0.8)


def zoom_out(shape):
    """최대 줌아웃 (버섯 아이콘 크기는 그대로, 한 화면에 더 넓게)."""
    global _zoomed
    if not ZOOM_OUT or sys.platform == "win32":
        return
    pinch(shape)
    _zoomed = True
    print("   광역 지도 줌아웃 (더 넓게)")


def goto_wide_map(tries=15, zoom=True):
    """블룸 첫 화면에서 광역 지도까지 자동으로: 안내 팝업 [OK] → 아래 나침반 버튼 → 아래 두 아이콘 중 오른쪽(광역 지도).
    새로 들어갔거나 아직 줌아웃 안 했으면 최대로 줌아웃."""
    entered = False
    for _ in range(tries):
        img = screencap()
        if VD is not None:
            save_public("scout_now.jpg", img, gallery=False)
        if find_ui(img, "toggle_wide"):
            if zoom and (entered or not _zoomed):
                zoom_out(img.shape)
            return True
        entered = True
        for name in ("ok", "compass", "toggle_normal"):  # 팝업이 위에 있으면 OK 부터
            p = find_ui(img, name)
            if p:
                if name == "toggle_normal":  # 두 아이콘 묶음의 가운데 → 오른쪽 아이콘으로
                    p = (p[0] + 48 * img.shape[1] / REF_W, p[1])
                print(f"   블룸 화면 넘기기: {name}")
                tap(*p)
                break
        time.sleep(2.5)  # 로딩 중이면 그냥 기다림
    print("   광역 지도로 못 들어갔습니다 (메뉴 5번 사진으로 지금 화면 확인)")
    return False


def back_to_pikmin():
    launch_pikmin()
    time.sleep(2.5)
    for _ in range(3):  # 상세 화면이 남아 있으면 뒤로 가서 지도로
        img = screencap()
        if not find_ui(img, "move_here") and not find_ui(img, "open_map"):
            break
        adb("shell", "input", "keyevent", "4")
        time.sleep(1.5)
    goto_wide_map(5)


def save_public(name, img, gallery=True):
    """Download 폴더에 확인용 사진 저장 (앱의 확인용 사진에서 봄). gallery=True 면 갤러리에도 보이게 등록."""
    path = "/storage/emulated/0/Download/" + name
    tmp = path + ".tmp.jpg"  # 다 쓴 뒤 한 번에 바꿔치기: 앱 화면이 쓰는 도중의 파일을 읽어 엑스박스가 뜨지 않게
    if not cv2.imwrite(tmp, img, [cv2.IMWRITE_JPEG_QUALITY, 70]):  # 폴더 없음(PC 등) → 건너뜀
        return path
    os.replace(tmp, path)
    if gallery:
        adb("shell", f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d file://{path}", check=False, timeout=10)
    return path


def press(x, y, ms=80):
    """손가락 내림 → 잠깐 → 올림 을 따로 보낸다 (input motionevent, 안드로이드 11+). 안 되는 기기면 보통 누르기."""
    x, y = str(int(x)), str(int(y))
    if "Error" in adb("shell", "input", "motionevent", "DOWN", x, y, check=False):
        return tap(x, y)
    time.sleep(ms / 1000)
    adb("shell", "input", "motionevent", "UP", x, y, check=False)


def touch_check():
    """버섯이 안 눌릴 때: 블룸이 터치 자체를 받는지 아래 지도 전환 버튼으로 확인 (눌러 보고 다시 광역 지도로)."""
    img = screencap()
    p = find_ui(img, "toggle_wide")
    if not p:
        return "광역 지도 버튼이 안 보임 (팝업 등)"
    tap(p[0] - 48 * img.shape[1] / REF_W, p[1])  # 왼쪽 아이콘(일반 지도)
    time.sleep(2)
    moved = find_ui(screencap(), "toggle_normal") is not None
    goto_wide_map(5)
    return "블룸이 버튼 터치에는 반응함 → 버섯 누르는 자리 문제" if moved else "블룸이 버튼 터치에도 반응 안 함 → 터치 자체가 안 들어감"


def settle(img, tpls, want, x, y, tries=6):
    """지도가 드래그 뒤 미끄러지는 중이면 버섯 누르기가 '멈춤'으로만 먹힌다 → 화면이 멈출 때까지 기다린 뒤
    멈춘 화면에서 버섯 위치를 다시 잡아 돌려준다."""
    small = lambda im: cv2.resize(im, (64, 128), interpolation=cv2.INTER_AREA)
    prev = small(img)
    for _ in range(tries):
        time.sleep(0.5)
        img = screencap()
        cur = small(img)
        still = float(np.mean(cv2.absdiff(prev, cur))) < STILL_DIFF
        prev = cur
        if still:
            break
    w = img.shape[1]
    near = sorted((t for t in detect(img, tpls) if want in t[0]), key=lambda t: (t[2] - x) ** 2 + (t[3] - y) ** 2)
    if near and (near[0][2] - x) ** 2 + (near[0][3] - y) ** 2 < (0.15 * w) ** 2:
        if (near[0][2], near[0][3]) != (x, y):
            print(f"   멈춘 화면에서 다시 잡음 ({x},{y}) → ({near[0][2]},{near[0][3]})")
        return near[0][2], near[0][3]
    return x, y


def fetch_coords(x, y):
    """버섯 → 이름표 → 여기로 이동 → 지도 앱 열기 → 좌표 읽기 → 피크민으로 복귀."""
    adb("shell", f"am force-stop {MAPS_PKG}", check=False)  # 지도 앱 기록에 이전 주소가 남지 않게
    before = map_queries()  # 그래도 남아 있는 예전 주소 (최근 앱 기록 등)
    label, shot0 = None, screencap()  # 누르기 직전 화면 (실패하면 누른 뒤 화면과 나란히 저장)
    w = shot0.shape[1]
    # 버섯의 '눌리는 자리'는 보이는 갓이 아니라 밑동(땅에 닿은 곳)일 수 있다 → 갓 → 조금 아래 → 밑동,
    # 마지막은 2초 쉬고 손가락 내림/올림만 따로 보내는 방식으로
    # 블룸은 '짧게 눌렀다 떼면' 이름표가 뜬다 → 모두 짧게
    tries = [("갓", x, y, TAP_MS), ("조금 아래", x, y + int(0.05 * w), TAP_MS),
             ("밑동", x, y + int(0.09 * w), TAP_MS), ("갓(다른 방식)", x, y, 0)]
    for name, tx, ty, ms in tries:
        if ms:
            tap(tx, ty, ms)
        else:
            time.sleep(2)
            press(tx, ty)
        for _ in range(3):
            time.sleep(1)
            img = screencap()
            label = find_label(img, x, y, shot0)
            if label:
                break
        if label:
            print(f"   이름표 뜸 (누른 자리: {name})")
            # 성공한 순간도 남겨 실패 사진과 비교할 수 있게 (왼쪽 누르기 전, 오른쪽 이름표 뜬 뒤)
            save_public("scout_label.jpg", np.hstack([annotate(shot0, [(name, 0, tx, ty)]), annotate(img, [("label", 0, *label)])]))
            break
    if not label:
        marks = [(f"t{i + 1}", 0, tx, ty) for i, (_, tx, ty, _) in enumerate(tries[:3])]
        save_public("scout_nolabel.jpg", np.hstack([annotate(shot0, marks), annotate(img, marks)]))
        print("   이름표를 못 찾음 → 확인용 사진 (왼쪽 = 누르기 전, 오른쪽 = 누른 뒤, 빨간 원 = 누른 곳). 다음 바퀴에 다시 시도")
        print("   진단: " + touch_check())
        return None
    tap(*label)
    time.sleep(2.5)
    for name in ("move_here", "open_map"):
        for _ in range(3):
            p = find_ui(screencap(), name)
            if p:
                break
            time.sleep(1)
        else:
            print(f"   '{name}' 버튼을 못 찾음")
            back_to_pikmin()
            return None
        tap(*p)
        time.sleep(1.2)
    coords = read_map_coords(exclude=before)
    if not coords:
        print("   지도 앱에서 새 좌표를 못 읽음")
    back_to_pikmin()
    return coords


def report(lat, lng, people=0):
    txt = f"{lat},{lng}"
    with open(FOUND, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M ") + f"{txt} 인원 {people} {WANT_NAME}\n")
    termux_api("termux-clipboard-set", txt)
    alert(f"인원 {people}명 · {txt} (버섯찾기 메뉴 4번에서 지도로 열기)")


def audit_add(img, name, x, y, people, coords, status):
    """검수 기록: 후보 화면(작게)과 인원·좌표·판정을 audit/ 에 남긴다. 최근 AUDIT_MAX 개만."""
    os.makedirs(AUDIT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}"
    pic = cv2.resize(annotate(img, [(name, 0, x, y)]), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    cv2.imencode(".jpg", pic, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tofile(os.path.join(AUDIT_DIR, stamp + ".jpg"))
    path = os.path.join(AUDIT_DIR, "audit.json")
    rows = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    rows.insert(0, {"time": time.strftime("%Y-%m-%d %H:%M"), "img": stamp + ".jpg", "people": people, "status": status,
                    "lat": coords[0] if coords else None, "lng": coords[1] if coords else None})
    for old in rows[AUDIT_MAX:]:
        try:
            os.remove(os.path.join(AUDIT_DIR, old["img"]))
        except OSError:
            pass
    json.dump(rows[:AUDIT_MAX], open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"   검수 기록: {status}" + (f" · {coords[0]},{coords[1]}" if coords else " · 좌표 없음"))


def already_found(lat, lng, hours=24):
    """최근 내역에 거의 같은 자리(약 50m 안)가 있으면 True - 껐다 켜도 같은 버섯을 또 알리지 않게."""
    if not os.path.exists(FOUND):
        return False
    since = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() - hours * 3600))
    for line in open(FOUND, encoding="utf-8"):
        m = COORD_RE.search(line)
        if m and line[:16] >= since and abs(float(m.group(1)) - float(lat)) < 5e-4 and abs(float(m.group(2)) - float(lng)) < 5e-4:
            return True
    return False


def battery_low(limit=15):
    """충전 중이 아닌데 배터리가 limit% 이하면 True."""
    b = adb("shell", "dumpsys battery", check=False)
    level = re.search(r"level: (\d+)", b)
    charging = re.search(r"(AC|USB|Wireless) powered: true", b)
    return bool(level) and int(level.group(1)) <= limit and not charging


def stuck_report(img):
    """지도가 안 움직임(팝업·로딩·로그아웃 등): 지금 화면을 사진으로 남기고 알린다."""
    path = save_public("scout_stuck.jpg", img)
    notify("버섯 탐지기: 지도가 안 움직여요", "블룸에 팝업이 떴을 수 있어요. 메뉴 5번(확인용 사진)으로 보고, 메뉴 1번으로 직접 닫아 주세요")
    print(f"   지도가 3번 연속 안 움직여서 멈춥니다. 지금 화면 사진: {path}")


def notify(title, text):
    """폰 알림 띄우기. Shizuku(셸 권한)의 cmd notification 이라 추가 앱이 필요 없다."""
    if sys.platform != "win32":
        adb("shell", f"cmd notification post -S bigtext -t {shlex.quote(title)} scout{random.randint(0, 9999)} {shlex.quote(text)}", check=False)


def history():
    """찾은 버섯 내역. 번호를 고르면 구글 지도로 연다."""
    rows = open(FOUND, encoding="utf-8").read().split("\n")[::-1] if os.path.exists(FOUND) else []
    rows = [r for r in rows if COORD_RE.search(r)]
    if not rows:
        return print("아직 찾은 버섯이 없습니다.")
    while True:
        print("\n=== 버섯 내역 (최근 순) ===")
        for i, r in enumerate(rows, 1):
            print(f"{i:>3}. {r}")
        n = input("\n지도로 열 번호 (그냥 엔터 = 닫기): ").strip()
        if not n.isdigit() or not 1 <= int(n) <= len(rows):
            return
        lat, lng = COORD_RE.search(rows[int(n) - 1]).groups()
        subprocess.run(["termux-open-url", f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"])


def termux_api(*cmd):
    """Termux:API 명령. 앱이 없거나 권한이 없으면 응답 없이 멈추므로 5초만 기다린다."""
    if not shutil.which(cmd[0]):
        return
    try:
        subprocess.run(list(cmd), timeout=5, capture_output=True)
    except subprocess.TimeoutExpired:
        print(f"   ({cmd[0]} 응답 없음: Termux:API 앱 설치/권한 확인)")


def alert(msg):
    print("발견! " + msg + "\a")
    if sys.platform == "win32":
        import winsound
        winsound.Beep(1200, 600)
    else:
        notify(f"{WANT_NAME} 발견!", msg)


def annotate(img, hits):
    out = img.copy()
    for n, sc, x, y in hits:
        cv2.circle(out, (x, y), 40, (0, 0, 255), 3)
        cv2.putText(out, f"{n} {sc:.2f}", (x - 40, y - 48), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    return out


def save(folder, img, keep=30):
    os.makedirs(os.path.join(HERE, folder), exist_ok=True)
    p = os.path.join(HERE, folder, time.strftime("%Y%m%d_%H%M%S") + f"_{random.randint(0, 999):03d}.jpg")
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tofile(p)  # 한글 경로 대응, 용량 작게
    for old in sorted(glob.glob(os.path.join(HERE, folder, "*")))[:-keep]:  # 오래된 것부터 지워 keep 장만 남김
        os.remove(old)
    return p


def ensure_pikmin_front():
    """기본 모드: 블룸이 앞에 없으면(홈 화면, 다른 앱) 드래그가 거기 먹히므로 블룸으로 돌아올 때까지 멈춰 기다린다.
    백그라운드 모드: 가상 화면에서 블룸이 사라졌으면 다시 띄운다."""
    if VD is None:
        told = False
        while PIKMIN_PKG not in adb("shell", "dumpsys window | grep mCurrentFocus", check=False):
            if not told:
                print("   블룸이 화면에 없어서 멈춤 - 블룸으로 돌아오면 이어서 찾습니다 (폰을 쓰려면 백그라운드 버튼)")
                told = True
            time.sleep(3)
        return
    # 가상 화면: 창 목록(dumpsys)은 기기마다 달라 오판 → 블룸 프로세스가 살아 있는지만 본다. 화면 상태는 탐색 중 광역 지도 버튼으로 확인
    if not any(adb("shell", f"pidof {PIKMIN_PKG}", check=False).strip() or time.sleep(1) for _ in range(3)):
        print("   피크민 블룸이 꺼져 있어서 다시 띄웁니다")
        launch_pikmin()
        time.sleep(8)
        goto_wide_map()


STOP_AT = 0  # 자동 종료 시각 (--max-minutes)


def stop_check():
    """중지 버튼이 눌렸으면(떠 있는 버튼 로그에 STOP) Ctrl+C 와 똑같이 멈춘다. 자동 종료 시각이 지나도 멈춘다."""
    if STOP_AT and time.time() > STOP_AT:
        print("정한 시간이 지나서 자동으로 멈춥니다.")
        notify("버섯 탐지기: 정한 시간이 끝나 멈췄어요", "다시 찾으려면 앱에서 [찾기 시작]")
        raise KeyboardInterrupt
    if STOPPED.is_set() or (OVERLAY_ON and "STOP" in adb("shell", f"cat {OV_LOG}", check=False)):
        raise KeyboardInterrupt


_dex_ok = False


def install_dex():
    """도우미(dex)를 셸이 읽는 곳에 복사. 한 번 실행에 한 번만 (Shizuku 명령도 발열)."""
    global _dex_ok
    if _dex_ok:
        return
    _dex_ok = True
    os.makedirs(VD_DIR, exist_ok=True)
    shutil.copy(os.path.join(HERE, "scout_display.dex"), VD_DIR + "/scout_display.dex")
    adb("shell", f"rm -f {VD_DEX}; cp {VD_DIR}/scout_display.dex {VD_DEX} && chmod 444 {VD_DEX}")  # 읽기 전용이라 지우고 새로


def spawn(args, log):
    """도우미를 폰(셸) 쪽에서 따로 띄운다. Termux 연결과 상관없이 돌고, 출력은 셸 쪽 log 파일에 남는다."""
    # setsid: rish 가 끝날 때 같은 프로세스 묶음째 정리되며 도우미까지 죽지 않게 따로 떼어 낸다
    # 뒤에서 도는 프로그램 때문에 rish 가 안 끝날 수 있어 8초만 기다리고 넘어간다(도우미는 계속 돈다)
    try:
        subprocess.run(_cmd("shell", f"CLASSPATH={VD_DEX} setsid nohup app_process / ScoutDisplay {args} >{log} 2>&1 </dev/null &"),
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)  # 파이프가 없어야 시간 초과 때 바로 풀린다
    except subprocess.TimeoutExpired:
        pass


def overlay_start():
    """블룸(어느 화면이든) 위에 떠 있는 [중지] 버튼. 못 띄워도 탐색은 계속."""
    global OVERLAY_ON
    adb("shell", "pkill -f 'ScoutDisplay [o]verlay'; appops set com.android.shell SYSTEM_ALERT_WINDOW allow", check=False)
    spawn(f"overlay {VD_DIR}", OV_LOG)
    OVERLAY_ON = True


def overlay_stop():
    global OVERLAY_ON
    if OVERLAY_ON:
        open(VD_DIR + "/overlay_close", "w").close()
        time.sleep(1)
        adb("shell", "pkill -f 'ScoutDisplay [o]verlay'", check=False)
        OVERLAY_ON = False


def vd_start():
    """가상 화면을 만들고 피크민 블룸을 그 안에서 다시 띄운다."""
    global VD
    print("[1/4] 화면 크기 확인")
    h, w = screencap().shape[:2]  # 폰 화면과 같은 크기로 (wm size 는 rish 에서 빈 값이 나올 때가 있음)
    dpi = int((re.findall(r"\d+", adb("shell", "wm density", check=False)) or ["420"])[-1])
    # 발열 줄이기: 화면 크기·dpi 를 같은 비율로 줄이면 블룸 화면 배치는 그대로, 그리는 양만 줄어든다
    w, h, dpi = (int(v * VD_SCALE) // 2 * 2 for v in (w, h, dpi))
    print("[2/4] 도우미 설치")
    install_dex()
    adb("shell", "pkill -f 'ScoutDisplay [0-9]'", check=False)  # 지난번에 남은 것
    print("[3/4] 가상 화면 만드는 중 (최대 30초)")
    spawn(f"{w} {h} {dpi} {VD_DIR}", VD_LOG)
    log, m = "", None
    for _ in range(20):
        time.sleep(1)
        log = adb("shell", f"cat {VD_LOG}", check=False)
        m = re.search(r"display (\d+)", log)
        if m or "Exception" in log or "Error" in log:
            break
    if not m:
        sys.exit("가상 화면을 못 만들었습니다. 아래 내용을 캡처해 보내 주세요:\n" + (log.strip()[-1500:] or "(도우미 출력 없음)"))
    VD = int(m.group(1))
    print(f"[4/4] 가상 화면 {VD}번 ({w}x{h}) 에서 피크민 블룸 실행 중 (15초). 이제 폰은 자유롭게 쓰거나 화면을 꺼도 됩니다.")
    adb("shell", f"am force-stop {PIKMIN_PKG}", check=False)  # 폰 화면에 떠 있던 것을 내리고 가상 화면에서 새로
    launch_pikmin()
    time.sleep(8)  # 블룸 켜지는 동안 (그 뒤 팝업·나침반·광역 지도는 goto_wide_map 이 누른다)


def vd_stop():
    global VD
    open(VD_DIR + "/stop", "w").close()
    time.sleep(1)
    adb("shell", "pkill -f 'ScoutDisplay [0-9]'", check=False)
    VD = None


def vdtest():
    """백그라운드 모드 점검: 가상 화면에 피크민이 보이는지, 폰 화면을 꺼도 계속 움직이는지."""
    out = "/storage/emulated/0/Download/scout_vdtest"
    vd_start()
    try:
        a = screencap()
        cv2.imwrite(out + "_1.png", a)
        print(f"1) 저장: {out}_1.png  (갤러리/파일 앱 Download 에서 피크민 지도가 보이는지 확인)")
        print("2) 지금 전원 버튼으로 폰 화면을 끄고, 1분 뒤에 다시 켜세요...")
        time.sleep(30)
        b = screencap()
        time.sleep(20)
        c = screencap()
        cv2.imwrite(out + "_2.png", c)
        moved = float(np.mean(cv2.absdiff(b, c)))
        print(f"   화면 꺼진 동안 그림 변화 {moved:.1f} ({'계속 돌아감 OK' if moved > 0.5 else '멈춘 듯 - 결과 알려주세요'})")
        print("3) 지도 앱 주소 읽기 확인:", QUERY_RE.search(adb("shell", "dumpsys activity activities", check=False)) or "기록 없음(정상)")
    finally:
        vd_stop()


def scan(cells, want, rounds, max_people, once, bg=False):
    global WANT_NAME
    WANT_NAME = all_targets().get(want, want or "버섯")
    if bg:
        vd_start()
        adb("shell", "dumpsys deviceidle disable", check=False)  # 화면 꺼진 채 오래 둬도 절전(도즈)으로 인터넷이 끊기지 않게
        try:
            return scan(cells, want, rounds, max_people, once)
        finally:
            adb("shell", "dumpsys deviceidle enable", check=False)
            vd_stop()
    tpls = load_templates()
    tpls.update(load_templates(MY_DIR))  # 직접 등록한 버섯 (절대경로라 그대로 붙음)
    if want:  # 지금 찾는 버섯 사진만 비교 (나머지는 계산만 잡아먹어 발열)
        tpls = {k: v for k, v in tpls.items() if want in k}
    if not tpls:
        sys.exit("templates/ 에 버섯 이미지가 없습니다.")
    print(f"템플릿 {len(tpls)}개: {', '.join(tpls)} · 인원 {max_people}명 이하만")
    print("멈추기: 이 창에서 엔터 (다른 앱 쓰는 중이면 Termux 알림을 눌러 이 창을 연 뒤 엔터)")
    STOPPED.clear()
    if sys.platform != "win32":
        # ponytail: 떠 있는 [중지] 버튼(overlay_start)은 이 폰에서 막혀 꺼 둠. 엔터로 멈춘다
        if sys.stdin.isatty():
            threading.Thread(target=lambda: (input(), STOPPED.set()), daemon=True).start()
    # 훑는 동안 화면 자동 꺼짐을 30분으로. 끝나면(중지 포함) 원래대로
    keep = {} if VD is not None else {"screen_off_timeout": "1800000"}
    old = {k: adb("shell", f"settings get system {k}", check=False).strip() for k in keep}
    for k, v in keep.items():
        adb("shell", f"settings put system {k} {v}", check=False)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # Termux 창을 닫아도 finally 로 복구
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, lambda *_: sys.exit(0))
    try:
        _scan(tpls, cells, want, rounds, max_people, once)
    except KeyboardInterrupt:
        print("\n중지했습니다.")
    except Exception as e:
        notify("버섯 탐지기가 멈췄습니다", str(e))
        raise
    finally:
        for k, v in old.items():
            if v.isdigit():
                adb("shell", f"settings put system {k} {v}", check=False)


def _scan(tpls, cells, want, rounds, max_people, once):
    launch_pikmin()
    time.sleep(3)  # 피크민 블룸을 앞으로 띄우고 지도가 그려질 때까지
    if not goto_wide_map():
        notify("버섯 탐지기: 광역 지도로 못 들어감", "메뉴 5번 사진으로 블룸 화면을 확인해 주세요")
        return
    reported = []  # 이미 처리한 버섯의 지도상 위치(시작점 기준 픽셀) - 같은 버섯을 또 누르지 않게
    audited = []  # 검수 사진을 찍은 버섯 위치 (같은 버섯을 또 누르지 않게)
    prev, still = None, 0  # 직전 화면, 연속으로 안 움직인 횟수
    pos, shape = (0, 0), screencap().shape
    walk = explore()  # 한 번 만든 순서를 바퀴가 바뀌어도 이어 감 → 본 곳은 다시 안 보고 계속 바깥쪽으로
    cell = next(walk)
    for r in (itertools.count() if rounds == 0 else range(rounds)):
        i = 0
        while i < cells:
            travel(shape, pos, cell)
            pos, i = cell, i + 1
            time.sleep(random.uniform(0.8, 1.4))  # 지도 렌더링 대기 + 일정한 간격 피하기
            if VD is None or i % 5 == 1:  # 가상 화면은 5칸에 한 번만 확인 (Shizuku 명령도 발열)
                ensure_pikmin_front()
            img = screencap()
            if VD is not None:  # 안 보이는 가상 화면을 사진으로 확인할 수 있게 (갤러리 Download/scout_now.jpg)
                save_public("scout_now.jpg", img, gallery=False)
            small = cv2.resize(img, (64, 128), interpolation=cv2.INTER_AREA)
            still = still + 1 if prev is not None and float(np.mean(cv2.absdiff(prev, small))) < STILL_DIFF else 0
            prev = small
            if float(img.mean()) < 12:  # 화면이 까맣게 나옴(블룸이 멈췄거나 다시 그리는 중) → 멈추지 말고 블룸을 다시 띄움
                print("   화면이 까맣게 나와서 블룸을 다시 띄웁니다")
                launch_pikmin()
                time.sleep(8)
                goto_wide_map()
                prev, still = None, 0
                continue
            if still == 3:  # 3번 연속 안 움직임 → 광역 지도로 다시 들어가 보고, 6번이면 멈춤
                print("   지도가 3번 연속 안 움직여서 광역 지도로 다시 들어가 봅니다")
                goto_wide_map(5)
            if still >= 6:
                stuck_report(img)
                return
            if i % 10 == 0 and battery_low():
                notify("버섯 탐지기: 배터리 부족으로 멈춤", "충전기를 꽂고 다시 시작하세요")
                print("   배터리 15% 이하(충전 안 함)라서 멈춥니다.")
                return
            if VD is not None and not find_ui(img, "toggle_wide"):  # 팝업 등으로 광역 지도가 아니면 다시 들어감
                print("   광역 지도가 아니라서 다시 들어갑니다")
                goto_wide_map(5)
                img = screencap()
            sea = sea_ratio(img)
            hits = detect(img, tpls)
            print(f"[{r + 1}-{i}] 칸{cell} 바다 {sea:.0%} " + (", ".join(f"{n}({sc:.2f})" for n, sc, _, _ in hits) or "없음"))
            w, h = img.shape[1], img.shape[0]
            # 화면 가운데(이름표가 잘 보이는 자리)에 가까운 버섯부터
            cands = sorted((t for t in hits if want and want in t[0]),
                           key=lambda t: (t[2] - w / 2) ** 2 + (t[3] - 0.55 * h) ** 2)
            for n, _, x, y in cands:
                gx, gy = cell[0] * PAN[0] * w + x, cell[1] * PAN[1] * h + y  # 시작점 기준 지도 위치
                if any((gx - a) ** 2 + (gy - b) ** 2 < (0.25 * w) ** 2 for a, b in reported):
                    print(f"   {n} ({x},{y}) 이미 보낸 버섯")
                    continue
                cur, shift = img, None
                if not (0.25 * h < y < 0.85 * h and 0.06 * w < x < 0.94 * w):
                    # 가장자리: 누르면 이름표가 화면 밖에 뜸 → 지도를 끌어 이 버섯을 가운데로 옮긴 뒤 다시 잡는다
                    print(f"   {n} ({x},{y}) 가장자리라 가운데로 옮겨서 확인")
                    shift = (w / 2 - x, 0.55 * h - y)
                    drag(w, h, *shift)
                    time.sleep(1.2)
                    cur = screencap()
                    near = sorted((t for t in detect(cur, tpls) if want in t[0]
                                   and (t[2] - w / 2) ** 2 + (t[3] - 0.55 * h) ** 2 < (0.4 * w) ** 2),
                                  key=lambda t: (t[2] - w / 2) ** 2 + (t[3] - 0.55 * h) ** 2)
                    if not near:
                        print("   옮긴 뒤 다시 못 찾음")
                        drag(w, h, -shift[0], -shift[1])
                        continue
                    x, y = near[0][2], near[0][3]
                people = read_people(cur, x, y)
                print(f"   {n} ({x},{y}) 인원: {'읽기 실패' if people is None else people}")
                coords = None
                ok = people is not None and people <= max_people
                # 검수 모드: 조건에 안 맞는 후보도 사진+좌표를 남긴다 (알림·카운트 없음, 최대 AUDIT_MAX 장)
                check = AUDIT and len(audited) < AUDIT_MAX and not any(
                    (gx - a) ** 2 + (gy - b) ** 2 < (0.25 * w) ** 2 for a, b in audited)
                if ok or check:
                    x, y = settle(cur, tpls, want, x, y)
                    coords = fetch_coords(x, y)
                if check:
                    audited.append((gx, gy))
                    audit_add(cur, n, x, y, people, coords, "조건 맞음" if ok else
                              ("인원 못 읽음" if people is None else f"인원 {people}명이라 제외"))
                if shift:  # 지도를 원래 칸 위치로 되돌림 (격자 탐색 위치가 어긋나지 않게)
                    drag(w, h, -shift[0], -shift[1])
                    time.sleep(1)
                if people is None or people > max_people:
                    continue
                if coords:
                    reported.append((gx, gy))
                    if already_found(*coords):
                        print(f"   {coords[0]},{coords[1]} 은 최근 내역에 이미 있어서 알림 생략")
                        break
                    report(*coords, people)
                    save("hits", annotate(cur, hits))  # 알린 버섯만 기록 사진(최근 30장)
                    if once:
                        return
                break  # 화면이 바뀌었으니 다음 칸으로
            try:
                cell = walk.send(sea >= SEA_LIMIT)
            except StopIteration:  # 바다 등으로 더 갈 곳이 없음 → 처음 자리부터 다시
                print("더 갈 새 지역이 없어서 처음 자리부터 다시 봅니다.")
                walk = explore()
                cell = next(walk)
        print(f"이번 판 {i}칸 확인. 한 바퀴 다 봤어요 → 잠깐 쉬고 더 바깥쪽 새 지역을 봅니다 (본 곳은 다시 안 봄).")
        if rounds == 0 or r + 1 < rounds:
            for _ in range(random.randint(20, 40)):  # 새 버섯·인원 변화 대기
                stop_check()
                time.sleep(1)


def selftest():
    rng = np.random.default_rng(0)
    bg = rng.integers(0, 255, (2000, REF_W, 3), np.uint8)
    tpl = np.zeros((60, 60, 3), np.uint8)
    cv2.circle(tpl, (30, 25), 22, (40, 40, 220), -1)
    cv2.rectangle(tpl, (24, 40), (36, 58), (230, 230, 230), -1)
    bg[300:360, 200:260] = tpl
    big = cv2.resize(tpl, None, fx=1.1, fy=1.1)
    bg[600:600 + big.shape[0], 100:100 + big.shape[1]] = big
    hits = detect(bg, {"red": tpl})
    assert len(hits) == 2, hits
    assert any(abs(x - 230) < 5 and abs(y - 330) < 5 for _, _, x, y in hits), hits
    assert sorted(DIGITS) == list("0123456789"), sorted(DIGITS)
    assert {"badge_icon", "move_here", "open_map"} <= set(UI), sorted(UI)
    assert COORD_RE.search('text="37.712716,-122.455983"').groups() == ("37.712716", "-122.455983")
    # 탐색: 바다 칸 너머로는 넓히지 않고, 본 칸은 다시 안 본다
    walk, order = explore(30), []
    cell = next(walk)
    try:
        while True:
            order.append(cell)
            cell = walk.send(cell[0] < 0)  # 서쪽은 모두 바다라고 치면
    except StopIteration:
        pass
    assert len(order) == len(set(order)) == 30 and order[0] == (0, 0), order
    assert not any(c[0] < -1 for c in order), order  # 바다(서쪽 첫 칸) 너머로 안 감
    assert {"ok", "compass", "toggle_normal", "toggle_wide"} <= set(UI), sorted(UI)  # 블룸 첫 화면 → 광역 지도
    # 좌표: 지도 앱 열기 전부터 있던 옛 주소는 빼고 새 주소만
    global adb
    real_adb, dumps = adb, iter(["query=37.111111,-122.111111", "query=37.111111,-122.111111 query=37.222222,-122.222222"])
    adb = lambda *a, **k: next(dumps)
    try:
        old = map_queries()
        assert read_map_coords(1, old) == ("37.222222", "-122.222222")
        # 가장자리 버섯을 가운데로 끌 때 손가락이 화면 안에서 움직인다
        swipes = []
        adb = lambda *a, **k: swipes.append([int(v) for v in a[3:7]])
        drag(1080, 2340, 540 - 1040, 0.55 * 2340 - 60)
        assert all(0 <= x <= 1080 and 0 <= y <= 2340 for s in swipes for x, y in (s[:2], s[2:])), swipes
    finally:
        adb = real_adb
    print("selftest ok:", hits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["grab", "scan", "selftest", "history", "vdtest"])
    ap.add_argument("--cells", type=int, default=40, help="한 판에 훑을 칸 수(시작 위치 주변부터)")
    ap.add_argument("--rounds", type=int, default=0, help="몇 판 반복할지 (0 = 멈출 때까지 계속)")
    ap.add_argument("--want", help="이 문자열이 이름에 든 템플릿을 찾으면 좌표를 얻어 알린다")
    ap.add_argument("--bg", action="store_true", help="백그라운드: 폰 화면 밖 가상 화면에서 탐색 (폰을 쓰거나 꺼도 됨)")
    ap.add_argument("--once", action="store_true", help="하나 찾으면 멈춤 (기본: 계속 찾으며 새 버섯마다 알림)")
    ap.add_argument("--max-people", type=int, default=4, help="인원 배지가 이 숫자 이하인 버섯만 (배지 없음 = 0명)")
    ap.add_argument("--max-minutes", type=int, default=0, help="이 시간(분)이 지나면 알아서 멈춤 (0 = 계속)")
    ap.add_argument("--vd-scale", type=float, default=1.0, help="백그라운드 가상 화면 크기 배율 (0.6~1, 낮을수록 발열 적음)")
    ap.add_argument("--audit", action="store_true", help="후보 검수: 조건에 안 맞는 후보도 사진+좌표 저장 (최대 30, 알림 없음)")
    ap.add_argument("--no-zoom", action="store_true", help="광역 지도 줌아웃 안 함")
    ap.add_argument("--tap-ms", type=int, default=TAP_MS, help="버섯 누르는 시간(ms). 이름표가 안 뜨면 늘려 본다")
    a = ap.parse_args()
    if a.mode == "grab":
        print("저장:", save("shots", screencap()))
    elif a.mode == "scan":
        TAP_MS = a.tap_ms
        VD_SCALE = min(1.0, max(0.5, a.vd_scale))
        ZOOM_OUT = not a.no_zoom
        AUDIT = a.audit
        STOP_AT = time.time() + a.max_minutes * 60 if a.max_minutes else 0
        scan(a.cells, a.want, a.rounds, a.max_people, a.once, a.bg)
    elif a.mode == "vdtest":
        vdtest()
    elif a.mode == "history":
        history()
    else:
        selftest()
