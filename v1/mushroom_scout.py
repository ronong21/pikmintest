"""피크민 블룸 버섯 탐지기.

adb(PC) 또는 Shizuku rish(폰 Termux)로 화면을 캡처하고, templates/ 의 버섯 이미지와 비교해 찾는다.
scan 모드는 지도를 드래그로 돌려 가며 훑고, --want 버섯을 찾으면 인원 배지를 읽어
max-people 이하일 때 버섯 → 이름표 → 여기로 이동 → 지도 앱 열기 를 눌러 좌표를 얻는다.

  python mushroom_scout.py grab                          # 현재 화면 저장
  python mushroom_scout.py scan --want fancy_giant       # 거대 화려한 버섯(4명 이하) 좌표 찾기
  python mushroom_scout.py selftest                      # 탐지 로직·템플릿 자체 점검
"""
import argparse, glob, itertools, os, random, re, shlex, shutil, signal, subprocess, sys, time

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
COORD_RE = re.compile(r"(-?\d{1,2}\.\d{4,}),\s*(-?\d{1,3}\.\d{4,})")  # 구글 지도 검색창의 "위도,경도"
THRESHOLD = 0.75          # 매칭 점수 하한. 오탐 많으면 올리고, 놓치면 내린다
SCALES = (0.85, 1.0, 1.15)  # 줌 차이 흡수
FAST = 0.5  # 비교할 때 화면을 이 배율로 줄인다(속도). 놓치면 0.7~1.0 으로 올린다
REF_W = 1019  # 템플릿을 자른 스크린샷의 가로 픽셀. 폰 해상도가 달라도 이 폭으로 맞춰 비교한다
# 광역 지도에서 드래그 한 번에 옮기는 거리(화면 가로, 세로 비율). 천천히 끌어야 관성으로 더 밀리지 않는다
PAN = (0.55, 0.30)
PAN_MS = 700
SEA_LIMIT = 0.35  # 화면의 이 비율 이상이 바다면 그 칸에서 더 나아가지 않는다
MOVES = {"동": (1, 0), "서": (-1, 0), "남": (0, 1), "북": (0, -1)}
FOUND = os.path.join(HERE, "found.txt")  # 찾은 버섯 내역 (홈 화면 [버섯내역] 버튼으로 본다)


def adb(*args, binary=False, check=True):
    if os.path.basename(ADB).startswith("rish"):  # "exec-out"/"shell" 은 빼고 rish 에 명령만 넘긴다
        cmd = [ADB, "-c", " ".join(args[1:])]
    else:
        cmd = [ADB] + (["-s", SERIAL] if SERIAL else []) + list(args)
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode and check:
        sys.exit(f"adb 실패: {r.stderr.decode(errors='ignore').strip()}\n"
                 "폰은 Shizuku 앱이 '실행 중'인지 확인하세요(재부팅하면 다시 시작해야 함). PC 는 USB 디버깅/ADB_SERIAL 확인.")
    return r.stdout if binary else r.stdout.decode(errors="ignore")


def screencap():
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
    h, w = shape[:2]
    dx, dy = MOVES[d]
    j = lambda: random.uniform(-0.03, 0.03)  # 매번 같은 궤적이 되지 않게 흔든다
    cx, cy = (0.5 + j()) * w, (0.45 + j()) * h  # 위아래 게임 버튼을 피한 가운데
    pts = [cx + dx * PAN[0] * w / 2, cy + dy * PAN[1] * h / 2, cx - dx * PAN[0] * w / 2, cy - dy * PAN[1] * h / 2]
    adb("shell", "input", "swipe", *(str(int(p)) for p in pts), str(PAN_MS + random.randint(-100, 100)))


def sea_ratio(img):
    """화면(위아래 버튼 줄 제외) 중 파란 바다 비율."""
    h = img.shape[0]
    a = cv2.resize(img[int(h * 0.08):int(h * 0.88)], None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA).astype(int)
    b, g, r = a[..., 0], a[..., 1], a[..., 2]
    return float(((b > g + 15) & (b > r + 40)).mean())


def explore(max_cells):
    """시작 칸(0,0)에서 가까운 칸부터 훑는 순서를 낸다. 바다 칸 너머로는 넓히지 않고, 본 칸은 다시 안 본다.
    yield 한 칸에 대해 send(바다인지) 로 결과를 돌려받는다."""
    seen, frontier = set(), {(0, 0)}
    while frontier and len(seen) < max_cells:
        cell = min(frontier, key=lambda c: (abs(c[0]) + abs(c[1]), c))
        frontier.discard(cell)
        seen.add(cell)
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


def tap(x, y):
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def find_ui(img, name, threshold=0.8):
    hits = detect(img, {name: UI[name]}, threshold)
    return hits[0][2:] if hits else None


def find_label(img, x, y):
    """버섯을 누르면 그 위에 뜨는 검은 이름표("거대 화려한 버섯")의 가운데. 없으면 None."""
    w = img.shape[1]
    n, _, st, cen = cv2.connectedComponentsWithStats((img.max(axis=2) < 60).astype(np.uint8))
    best = None
    for i in range(1, n):
        bw, bh = st[i, 2], st[i, 3]
        cx, cy = cen[i]
        if bw > 0.18 * w and 0.02 * w < bh < 0.15 * w and y - 0.45 * w < cy < y + 0.05 * w and abs(cx - x) < 0.35 * w:
            if best is None or abs(cy - y) < abs(best[1] - y):
                best = (int(cx), int(cy))
    return best


def read_map_coords(tries=8):
    """지도 앱 화면의 글자를 덤프해 "위도,경도" 를 찾는다."""
    for _ in range(tries):
        time.sleep(1.5)
        adb("shell", "uiautomator", "dump", UI_PATH, check=False)
        try:
            m = COORD_RE.search(open(UI_PATH, encoding="utf-8", errors="ignore").read())
        except OSError:
            continue
        if m:
            return m.group(1), m.group(2)
    return None


def back_to_pikmin():
    adb("shell", "monkey", "-p", PIKMIN_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    time.sleep(2.5)
    for _ in range(3):  # 상세 화면이 남아 있으면 뒤로 가서 지도로
        img = screencap()
        if not find_ui(img, "move_here") and not find_ui(img, "open_map"):
            return
        adb("shell", "input", "keyevent", "4")
        time.sleep(1.5)


def fetch_coords(x, y):
    """버섯 → 이름표 → 여기로 이동 → 지도 앱 열기 → 좌표 읽기 → 피크민으로 복귀."""
    tap(x, y)
    time.sleep(1.5)
    label = find_label(screencap(), x, y)
    if not label:
        print("   이름표를 못 찾음")
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
    coords = read_map_coords()
    back_to_pikmin()
    return coords


def report(lat, lng, people=0):
    txt = f"{lat},{lng}"
    with open(FOUND, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M ") + f"{txt} 인원 {people}\n")
    termux_api("termux-clipboard-set", txt)
    alert(f"인원 {people}명 · {txt} (홈 화면 [버섯내역]에서 지도로 열기)")


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
    print(msg + "\a")
    if sys.platform == "win32":
        import winsound
        winsound.Beep(1200, 600)
    else:
        notify("거대 화려한 버섯 발견!", msg)


def annotate(img, hits):
    out = img.copy()
    for n, sc, x, y in hits:
        cv2.circle(out, (x, y), 40, (0, 0, 255), 3)
        cv2.putText(out, f"{n} {sc:.2f}", (x - 40, y - 48), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    return out


def save(folder, img):
    os.makedirs(os.path.join(HERE, folder), exist_ok=True)
    p = os.path.join(HERE, folder, time.strftime("%Y%m%d_%H%M%S") + f"_{random.randint(0, 999):03d}.png")
    cv2.imencode(".png", img)[1].tofile(p)  # 한글 경로 대응
    return p


def ensure_pikmin_front():
    """다른 앱(알림, 전화 등)으로 넘어갔으면 피크민 블룸을 다시 앞으로."""
    if PIKMIN_PKG not in adb("shell", "dumpsys window | grep mCurrentFocus", check=False):
        print("   피크민 블룸이 앞에 없어서 다시 띄웁니다")
        adb("shell", "monkey", "-p", PIKMIN_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
        time.sleep(3)


def scan(cells, want, rounds, max_people, once):
    tpls = load_templates()
    if not tpls:
        sys.exit("templates/ 에 버섯 이미지가 없습니다.")
    print(f"템플릿 {len(tpls)}개: {', '.join(tpls)} · 인원 {max_people}명 이하만 · 멈추기: 볼륨아래+c")
    # 훑는 동안: 화면 자동 꺼짐 30분, 밝기 최저(꺼진 것처럼 어둡게 = 배터리 절약). 끝나면(중지 포함) 원래대로
    keep = {"screen_off_timeout": "1800000", "screen_brightness_mode": "0", "screen_brightness": "1"}
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
    adb("shell", "monkey", "-p", PIKMIN_PKG, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    time.sleep(3)  # 피크민 블룸을 앞으로 띄우고 지도가 그려질 때까지
    reported = []  # 이미 처리한 버섯의 지도상 위치(시작점 기준 픽셀) - 같은 버섯을 또 누르지 않게
    for r in (itertools.count() if rounds == 0 else range(rounds)):
        pos, shape = (0, 0), screencap().shape
        walk = explore(cells)
        cell, i = next(walk), 0
        while True:
            travel(shape, pos, cell)
            pos, i = cell, i + 1
            time.sleep(random.uniform(0.8, 1.4))  # 지도 렌더링 대기 + 일정한 간격 피하기
            ensure_pikmin_front()
            img = screencap()
            sea = sea_ratio(img)
            hits = detect(img, tpls)
            print(f"[{r + 1}-{i}] 칸{cell} 바다 {sea:.0%} " + (", ".join(f"{n}({sc:.2f})" for n, sc, _, _ in hits) or "없음"))
            if want and any(want in n for n, *_ in hits):
                print("   저장:", save("hits", annotate(img, hits)))
            w, h = img.shape[1], img.shape[0]
            for n, _, x, y in hits:
                if not want or want not in n:
                    continue
                gx, gy = cell[0] * PAN[0] * w + x, cell[1] * PAN[1] * h + y  # 시작점 기준 지도 위치
                if any((gx - a) ** 2 + (gy - b) ** 2 < (0.25 * w) ** 2 for a, b in reported):
                    print(f"   {n} ({x},{y}) 이미 보낸 버섯")
                    continue
                people = read_people(img, x, y)
                print(f"   {n} ({x},{y}) 인원: {'읽기 실패' if people is None else people}")
                if people is None or people > max_people:
                    continue
                coords = fetch_coords(x, y)
                if coords:
                    reported.append((gx, gy))
                    report(*coords, people)
                    if once:
                        return
                break  # 화면이 바뀌었으니 다음 칸으로
            try:
                cell = walk.send(sea >= SEA_LIMIT)
            except StopIteration:
                break
        print(f"이번 판 {i}칸 확인. 처음 자리로 돌아가서 잠시 쉰 뒤 다시 훑습니다.")
        travel(shape, pos, (0, 0))
        if rounds == 0 or r + 1 < rounds:
            time.sleep(random.uniform(20, 40))  # 새 버섯·인원 변화 대기


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
    print("selftest ok:", hits)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["grab", "scan", "selftest", "history"])
    ap.add_argument("--cells", type=int, default=40, help="한 판에 훑을 칸 수(시작 위치 주변부터)")
    ap.add_argument("--rounds", type=int, default=0, help="몇 판 반복할지 (0 = 멈출 때까지 계속)")
    ap.add_argument("--want", help="이 문자열이 이름에 든 템플릿을 찾으면 좌표를 얻어 알린다")
    ap.add_argument("--once", action="store_true", help="하나 찾으면 멈춤 (기본: 계속 찾으며 새 버섯마다 알림)")
    ap.add_argument("--max-people", type=int, default=4, help="인원 배지가 이 숫자 이하인 버섯만 (배지 없음 = 0명)")
    a = ap.parse_args()
    if a.mode == "grab":
        print("저장:", save("shots", screencap()))
    elif a.mode == "scan":
        scan(a.cells, a.want, a.rounds, a.max_people, a.once)
    elif a.mode == "history":
        history()
    else:
        selftest()
