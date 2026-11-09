# 버섯 탐지기 앱 화면: 폰 브라우저에서 버튼으로 조작 (python scout_app.py)
# http://127.0.0.1:8765 - 찾기 시작/중지, 가상 화면 보기, 찾은 내역, 확인용 사진, 새 버섯 등록, 모두 끄기
import base64, hashlib, json, os, re, shlex, shutil, signal, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import cv2
import numpy as np

import mushroom_scout as ms

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
LOG = os.path.join(ms.HERE, "scan.log")
DL = "/storage/emulated/0/Download"
PHOTOS = {"now": "scout_now.jpg", "label": "scout_label.jpg", "nolabel": "scout_nolabel.jpg", "stuck": "scout_stuck.jpg"}
# 앱 파일들의 내용으로 만든 버전 (업데이트되면 바뀜 → 켜져 있던 예전 서버를 새로 띄우는 기준)
VERSION = hashlib.md5(b"".join(open(os.path.join(ms.HERE, f), "rb").read()
                              for f in ("scout_app.py", "mushroom_scout.py", "app.html"))).hexdigest()[:8]
BUILD = open(os.path.join(ms.HERE, "version.txt"), encoding="utf-8").read().strip()     if os.path.exists(os.path.join(ms.HERE, "version.txt")) else ""  # 예: v58 · 2026-11-02 18:40
SCAN_STATE = os.path.join(ms.HERE, "scan_state.json")  # 지금 찾는 작업 정보 (앱 서버가 다시 켜져도 이어받게)
SOUND_OFF = os.path.join(ms.HERE, ".sound_off")  # 있으면 블룸 소리 끔
state = {"proc": None, "target": None, "bg": True, "started": None, "user_stop": False, "shizuku": None,
         "opts": {}, "restarts": 0, "update": False, "gps": None}
SETTINGS = {"max_people": (4, 0, 4), "cells": (40, 10, 120), "tap_ms": (100, 50, 1000), "max_minutes": (0, 0, 720), "vd_scale": (100, 50, 100), "zoom": (1, 0, 1), "audit": (1, 0, 1), "memory_hours": (2, 0, 24), "gps": (0, 0, 1)}  # 이름: (기본, 최소, 최대)


def watch_shizuku():
    """30초마다 Shizuku 연결 확인 (화면에 표시). rish 가 가끔 빈 결과를 줘서 두 번 연속 실패해야 '꺼짐'."""
    fails = 0
    time.sleep(5)  # 켜자마자 업데이트·정리로 바쁠 때는 건너뜀
    while True:
        try:
            ok = b"ok" in subprocess.run(ms._cmd("shell", "echo ok"), capture_output=True, timeout=15).stdout
        except Exception:
            ok = False
        fails = 0 if ok else fails + 1
        if ok or fails >= 2:
            state["shizuku"] = ok
        time.sleep(30 if ok else 8)


def sh(cmd):
    """셸(Shizuku) 명령. 실패해도 앱은 계속."""
    return ms.adb("shell", cmd, check=False)


def termux(*cmd):
    if shutil.which(cmd[0]):
        subprocess.run(list(cmd), capture_output=True)


class Adopted:
    """앱 서버가 꺼졌다 다시 켜졌을 때, 예전 서버가 띄운 찾기 작업을 끄지 않고 이어받는다 (Popen 처럼 쓰는 부분만)."""
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None if os.path.exists(f"/proc/{self.pid}") else 0

    def send_signal(self, sig):
        try:
            os.kill(self.pid, sig)
        except OSError:
            pass

    def wait(self, timeout):
        for _ in range(int(timeout)):
            if self.poll() is not None:
                return 0
            time.sleep(1)
        raise subprocess.TimeoutExpired("scan", timeout)

    def kill(self):
        self.send_signal(signal.SIGKILL)


def running():
    p = state["proc"]
    return p is not None and p.poll() is None


GIST = "https://gist.githubusercontent.com/ronong21/30919049d2c3c99b192e806267b0a481/raw/scout_setup.sh"
INSTALLED = os.path.expanduser("~/.scout_setup.sh")  # 위젯이 마지막으로 받아 설치한 묶음


def file_md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest() if os.path.exists(path) else ""


def fetch_bundle():
    """gist 에서 최신 설치 묶음을 받아 임시 파일 경로를 돌려준다 (실패하면 None)."""
    tmp = INSTALLED + ".new"
    r = subprocess.run(["curl", "-sfL", f"{GIST}?{int(time.time())}", "-o", tmp], capture_output=True, timeout=60)
    return tmp if r.returncode == 0 and os.path.getsize(tmp) > 1000 else None


def check_update():
    """gist 에 새 버전이 있는지 확인 → 화면에 [지금 업데이트] 표시. 결과(True/False)를 돌려준다."""
    try:
        tmp = fetch_bundle()
        state["update"] = bool(tmp) and file_md5(tmp) != file_md5(INSTALLED)
    except Exception:
        pass
    state["update_checked"] = time.time()
    return state["update"]


def watch_update():
    """5분마다 새 버전 확인 (화면을 다시 열 때·버전을 누를 때도 바로 확인)."""
    time.sleep(20)
    while True:
        check_update()
        time.sleep(300)


def do_update():
    """새 버전 설치 → 이 앱을 새 코드로 다시 실행. 찾는 중이었으면 같은 설정으로 이어서 찾는다."""
    tmp = fetch_bundle()
    if not tmp:
        return {"error": "새 버전을 받지 못했어요 (인터넷 확인)"}
    resume = json.dumps({"target": state["target"], "bg": state["bg"], "opts": state["opts"]}) if running() else ""
    stop()
    r = subprocess.run(["bash", tmp], stdin=subprocess.DEVNULL, capture_output=True, timeout=300)
    if r.returncode != 0:
        return {"error": "설치 실패: " + r.stdout.decode(errors="ignore").strip()[-200:]}
    os.replace(tmp, INSTALLED)

    def restart():
        time.sleep(1)  # 응답을 보낸 뒤 새 코드로 바꿔 실행 (포트는 닫혔다가 새 프로세스가 다시 연다)
        os.environ["SCOUT_RESUME"] = resume
        os.execv(sys.executable, [sys.executable, os.path.join(ms.HERE, "scout_app.py"), "--no-open"])
    threading.Thread(target=restart, daemon=True).start()
    return {"ok": "업데이트했어요. 잠시 뒤 새 버전으로 바뀝니다" + (" (찾기는 이어서 계속)" if resume else "")}


def watch_gps():
    """GPS 주말 모드 중엔 30초마다 같은 위치를 다시 넣어 준다 (안 넣으면 블룸이 위치 신호를 잃음)."""
    while True:
        time.sleep(30)
        if state["gps"]:
            ms.gps_set(*state["gps"][2:])


def gps_apply(on):
    """[찾기 시작] 때: 켜져 있으면 지금 주말인 도시로 위치 이동, 꺼져 있으면 원래 위치로. 진행 기록에 남길 문장을 돌려준다."""
    if not on:
        if state["gps"]:
            ms.gps_clear()
            state["gps"] = None
            return "GPS 주말 모드 꺼짐 → 원래 위치로 돌아왔어요\n"
        return ""
    spot = ms.gps_pick()
    if not spot:
        if state["gps"]:
            ms.gps_clear()
            state["gps"] = None
        return "GPS 주말 모드: 지금 주말인 도시가 없어서(한국 화~목) 위치를 옮기지 않고 내 자리에서 찾아요\n"
    state["gps"] = spot
    ms.gps_set(*spot[2:])
    time.sleep(3)  # 블룸이 새 위치를 받을 시간
    return f"GPS 주말 모드: 지금 주말인 {spot[0]}의 '{spot[1]}'({spot[2]},{spot[3]})로 위치를 옮겼어요\n"


def watch_crash():
    """찾기가 오류로 멈추면 30초 뒤 같은 설정으로 다시 시작 (한 번 [찾기 시작]에 최대 3번)."""
    while True:
        time.sleep(10)
        p = state["proc"]
        if p is None or p.poll() in (None, 0) or state["user_stop"] or state["restarts"] >= 3:
            continue
        time.sleep(30)
        if state["user_stop"] or running():
            continue
        state["restarts"] += 1
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"\n오류로 멈춰서 자동으로 다시 시작합니다 ({state['restarts']}/3)\n")
        start(state["target"], state["bg"], state["opts"], keep_log=True)


def start(target, bg, opts, keep_log=False):
    if running():
        return "이미 찾는 중입니다"
    if target not in ms.all_targets():
        return "모르는 버섯입니다"
    termux("termux-wake-lock")
    first = gps_apply(str(opts.get("gps", 0)) == "1") if not keep_log else ""
    cmd = [sys.executable, "-u", os.path.join(ms.HERE, "mushroom_scout.py"), "scan", "--want", target] + (["--bg"] if bg else [])
    for k, (dflt, lo, hi) in SETTINGS.items():  # 화면의 설정값 (범위 밖이면 기본값)
        try:
            v = int(opts.get(k, dflt))
        except (TypeError, ValueError):
            v = dflt
        v = v if lo <= v <= hi else dflt
        if k == "zoom":
            cmd += [] if v else ["--no-zoom"]
            continue
        if k == "gps":  # 위치 옮기기는 앱 서버가 함 (찾기를 멈춰도 그 자리에 남아 있게)
            continue
        if k == "audit":  # 거대 버섯을 찾을 때만 (잘 거르는지 확인용)
            name = ms.all_targets().get(target, "")
            cmd += ["--audit"] if v and ("giant" in target or "거대" in name) else []
            continue
        cmd += ["--" + k.replace("_", "-"), str(v / 100 if k == "vd_scale" else v)]
    if not keep_log:
        state["restarts"] = 0
        open(LOG, "w", encoding="utf-8").write(first)
    state.update(proc=subprocess.Popen(cmd, cwd=ms.HERE, stdin=subprocess.DEVNULL, stdout=open(LOG, "a"),
                                       stderr=subprocess.STDOUT), target=target, bg=bg, opts=opts,
                 started=state["started"] if keep_log else time.time(), user_stop=False)
    json.dump({k: state[k] for k in ("target", "bg", "opts", "started", "gps")}, open(SCAN_STATE, "w", encoding="utf-8"))
    return "시작했습니다"


def stop():
    p = state["proc"]
    state["user_stop"] = True
    if running():
        p.send_signal(signal.SIGINT)  # Ctrl+C 와 같음 → 가상 화면·설정 정리 후 끝남
        try:
            p.wait(25)
        except subprocess.TimeoutExpired:
            p.kill()
    termux("termux-wake-unlock")
    return "멈췄습니다"


def history():
    rows = []
    if os.path.exists(ms.FOUND):
        for line in open(ms.FOUND, encoding="utf-8"):
            m = ms.COORD_RE.search(line)
            if not m:
                continue
            ppl = re.search(r"인원 (\d+)", line)
            rows.append({"time": line[:16], "lat": m.group(1), "lng": m.group(2),
                         "people": int(ppl.group(1)) if ppl else None,
                         "name": line[line.find(ppl.group(0)) + len(ppl.group(0)):].strip() if ppl else ""})
    return rows[::-1]


def audit_rows():
    p = os.path.join(ms.HERE, "audit", "audit.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def cleanup(include_photos=True):
    """임시 파일 정리: 쓰다 만 사진(*.tmp.jpg), 가상 화면 작업 파일, 기록 사진(hits), (원하면) 확인용 사진.
    찾은 내역·검수 기록·등록한 버섯은 그대로. 지운 크기(KB)를 돌려준다."""
    import glob as g
    paths = g.glob(os.path.join(DL, "scout_*.tmp.jpg")) + g.glob(os.path.join(ms.HERE, "hits", "*")) + g.glob(INSTALLED + ".new")
    if include_photos and not running():  # 찾는 중엔 가상 화면 작업 파일·지금 화면 사진은 건드리지 않음 (캡처가 끊기지 않게)
        paths += [os.path.join(DL, f) for f in PHOTOS.values()] + g.glob(os.path.join(ms.VD_DIR, "cap.*"))
    elif include_photos:
        paths += [os.path.join(DL, f) for k, f in PHOTOS.items() if k != "now"]
    freed = 0
    for p in paths:
        try:
            freed += os.path.getsize(p)
            os.remove(p)
        except OSError:
            pass
    return freed // 1024


def reset_all():
    """처음부터 다시: 찾기 멈춤 → 찾은 내역·확인용 사진·기록 사진·검수 기록·진행 기록 모두 지움.
    (돌았던 곳 기록은 찾기 한 번 안에서만 쓰므로 다시 [찾기 시작]하면 처음 자리부터 새로 돈다)"""
    stop()
    for pid, kind in our_procs().items():  # 이 서버가 모르는(예전 서버가 띄운) 찾기 작업도 멈춰야 검수 기록이 다시 안 생김
        if kind == "scan":
            Adopted(pid).send_signal(signal.SIGINT)
            try:
                Adopted(pid).wait(25)
            except subprocess.TimeoutExpired:
                Adopted(pid).kill()
    for p in [ms.FOUND, LOG, ms.MEMORY] + [os.path.join(DL, f) for f in PHOTOS.values()]:
        try:
            os.remove(p)
        except OSError:
            pass
    for d in ("hits", "audit"):
        shutil.rmtree(os.path.join(ms.HERE, d), ignore_errors=True)
    state.update(started=None, target=None)
    return {"ok": "리셋했어요. [찾기 시작]하면 처음 자리부터 새로 돌아요"}


def history_delete(lat, lng):
    """내역에서 그 좌표 줄을 지운다 (lat/lng 둘 다 비면 전부)."""
    if not os.path.exists(ms.FOUND):
        return {"ok": "없음"}
    lines = open(ms.FOUND, encoding="utf-8").read().splitlines(True)
    keep = [l for l in lines if lat and f"{lat},{lng}" not in l]
    open(ms.FOUND, "w", encoding="utf-8").writelines(keep)
    return {"ok": "지웠습니다"}


def decode(data_url):
    raw = base64.b64decode(data_url.split(",", 1)[-1])
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def save_target(name, crop):
    """버섯 사진(REF_W 기준) 저장. 같은 이름이 있으면 그 버섯의 사진을 하나 더 추가(키 뒤에 _2, _3)."""
    os.makedirs(ms.MY_DIR, exist_ok=True)
    names_path = os.path.join(ms.MY_DIR, "names.json")
    names = json.load(open(names_path, encoding="utf-8")) if os.path.exists(names_path) else {}
    same = [key for key, n in names.items() if n == name]
    key = f"{min(same, key=len)}_{len(same) + 1}" if same else f"my{time.time_ns() // 1000000}"
    cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tofile(os.path.join(ms.MY_DIR, key + ".jpg"))
    names[key] = name
    json.dump(names, open(names_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return key


def register(data):
    """직접 고르기: 올린 스크린샷에서 손가락으로 고른 네모를 버섯 사진으로 저장."""
    name = data.get("name", "").strip()[:30]
    if not name:
        return {"error": "버섯 이름을 적어 주세요"}
    img = decode(data["image"])
    if img is None:
        return {"error": "사진을 읽을 수 없습니다"}
    x, y, w, h = (int(v) for v in data["box"])
    if w < 10 or h < 10:
        return {"error": "버섯 부분을 손가락으로 끌어서 네모로 골라 주세요"}
    ref, k = ms.to_ref(img)  # 탐지기와 같은 폭(REF_W)으로 맞춘 뒤 자른다
    crop = ref[int(y * k):int((y + h) * k), int(x * k):int((x + w) * k)]
    return {"ok": f"'{name}' 등록 완료", "key": save_target(name, crop)}


# 이름표 아래 버섯 위치 (REF_W 기준, 보내 준 거대·중형 이름표 화면에서 잰 값): 이름표 가운데에서 dx, 아래쪽 끝에서 dy, 자를 크기
LABEL_CROP = {"거대": (-10, 100, 104, 72), "중형": (-10, 100, 104, 72), "소형": (12, 90, 88, 54)}  # 소형은 얼음 파랑버섯 이름표 화면(기본·줌아웃)에서 잼


def register_point(data):
    """이름표가 잘린 사진: 사용자가 사진에서 버섯을 한 번 누른 자리를 가운데로 잘라 등록."""
    size, base = data.get("size"), data.get("base", "").strip()[:24]
    img = decode(data.get("image", ""))
    if size not in LABEL_CROP or not base or img is None:
        return {"error": "사진이나 이름이 없어요"}
    ref, k = ms.to_ref(img)
    _, _, cw, ch = LABEL_CROP[size]
    cx, cy = int(float(data["x"]) * k), int(float(data["y"]) * k)
    crop = ref[max(cy - ch // 2, 0):cy + ch // 2, max(cx - cw // 2, 0):cx + cw // 2]
    if crop.shape[0] < ch // 2 or crop.shape[1] < cw // 2:
        return {"error": "버섯 가운데를 눌러 주세요"}
    name = f"{size} {base}"
    save_target(name, crop)
    return {"ok": f"등록 완료: {name}", "preview": {"name": name, "img": "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", crop)[1]).decode()}}


def find_any_label(ref):
    """화면에서 검은 이름표("거대 ○○ 버섯 / 장소") 찾기 → (x, y, w, h). 여러 개면 가장 큰 것."""
    w = ref.shape[1]
    n, _, st, _ = cv2.connectedComponentsWithStats((ref.max(axis=2) < 60).astype(np.uint8))
    boxes = [tuple(int(v) for v in st[i, :4]) for i in range(1, n)
             if st[i, 2] > 0.18 * w and 0.02 * w < st[i, 3] < 0.30 * w and st[i, 2] < 0.8 * w]
    return max(boxes, key=lambda b: b[2] * b[3]) if boxes else None


def auto_register(data):
    """이름표가 뜬 스크린샷(크기별 한 장씩)에서 이름표 바로 아래 버섯을 자동으로 잘라 등록."""
    base = data.get("base", "").strip()[:24]
    if not base:
        return {"error": "버섯 이름을 적어 주세요 (예: 반짝이는 버섯)"}
    done, errors, previews, res_point = [], [], [], []
    for item in data.get("items", []):
        size = item.get("size")
        img = decode(item.get("image", ""))
        if size not in LABEL_CROP or img is None:
            errors.append(f"{size}: 사진을 읽을 수 없어요")
            continue
        ref, _ = ms.to_ref(img)
        lab = find_any_label(ref)
        if not lab:
            errors.append(f"{size}: 검은 이름표를 못 찾았어요. 버섯을 눌러 이름표가 보이는 화면을 찍어 주세요")
            continue
        if lab[0] <= 2 or lab[0] + lab[2] >= ref.shape[1] - 2:  # 이름표가 화면 끝에 잘림 → 버섯 위치를 몰라서 화면에서 한 번 눌러 달라고
            res_point.append(size)
            continue
        dx, dy, cw, ch = LABEL_CROP[size]  # 이름표 아래쪽 끝 기준이라 3줄짜리 긴 이름표도 같은 자리
        cx, cy = lab[0] + lab[2] // 2 + dx, lab[1] + lab[3] + dy
        y0, x0 = max(cy - ch // 2, 0), max(cx - cw // 2, 0)
        crop = ref[y0:y0 + ch, x0:x0 + cw]
        if crop.shape[0] < ch // 2 or crop.shape[1] < cw // 2:
            errors.append(f"{size}: 버섯이 화면 끝에 걸려 있어요. 버섯이 화면 가운데쯤 오게 찍어 주세요")
            continue
        name = f"{size} {base}"
        save_target(name, crop)
        done.append(name)
        previews.append({"name": name, "img": "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", crop)[1]).decode()})
    res = {"previews": previews, "need_point": res_point}
    if done:
        res["ok"] = "등록 완료: " + ", ".join(done)
    if errors:
        res["error"] = " / ".join(errors)
    return res


def targets():
    """화면에 보일 버섯 목록: 같은 이름의 추가 사진(_2 등)은 하나로 묶는다."""
    out = []
    for key, name in ms.all_targets().items():
        if re.search(r"_\d+$", key) and key.rsplit("_", 1)[0] in ms.all_targets():
            continue
        out.append({"key": key, "name": name, "mine": key.startswith("my")})
    return out


def delete_target(key):
    if not key.startswith("my"):
        return {"error": "기본 버섯은 지울 수 없습니다"}
    names_path = os.path.join(ms.MY_DIR, "names.json")
    names = json.load(open(names_path, encoding="utf-8"))
    for k in [k for k in names if k == key or k.startswith(key + "_")]:
        names.pop(k)
        p = os.path.join(ms.MY_DIR, k + ".jpg")
        if os.path.exists(p):
            os.remove(p)
    json.dump(names, open(names_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"ok": "지웠습니다"}


def quit_all():
    """찾기 중지 → 블룸 끄기 → 앱 서버와 Termux 끄기 (Shizuku 는 켜 둠: 다시 켜려면 와이파이가 필요해서)."""
    stop()
    if state["gps"]:
        ms.gps_clear()
    sh(f"pkill -f 'Scout[D]isplay'; dumpsys deviceidle enable; am force-stop {ms.PIKMIN_PKG}")
    sh("nohup sh -c 'sleep 2; am force-stop com.termux' >/dev/null 2>&1 &")
    threading.Thread(target=lambda: (time.sleep(1), os._exit(0)), daemon=True).start()
    return {"ok": "모두 껐습니다. 이 창을 닫아도 됩니다."}


def tail(n=60):
    if not os.path.exists(LOG):
        return []
    with open(LOG, "rb") as f:
        f.seek(max(0, os.path.getsize(LOG) - 64 * 1024))  # 끝 64KB 만
        return f.read().decode("utf-8", errors="ignore").splitlines()[-n:]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json; charset=utf-8", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def file(self, path, ctype="image/jpeg"):
        if path and os.path.exists(path):
            self.send(open(path, "rb").read(), ctype)
        else:
            self.send({"error": "없음"}, code=404)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            return self.file(os.path.join(ms.HERE, "app.html"), "text/html; charset=utf-8")
        if path == "/example.jpg":  # 새 버섯 등록 예시
            return self.file(os.path.join(ms.HERE, "example_register.jpg"))
        if path == "/api/version":
            return self.send({"version": VERSION, "build": BUILD})
        if path == "/api/status":
            p = state["proc"]
            crashed = p is not None and p.poll() not in (None, 0) and not state["user_stop"]
            return self.send({"version": VERSION, "build": BUILD, "running": running(), "target": state["target"], "bg": state["bg"],
                              "started": state["started"], "log": tail(400), "gps": state["gps"],
                              "sound": not os.path.exists(SOUND_OFF), "crashed": crashed, "update": state["update"],
                              "audit": audit_rows(),
                              "shizuku": state["shizuku"], "settings": {k: v[0] for k, v in SETTINGS.items()},
                              "targets": targets(), "history": history(),
                              "photos": {k: int(os.path.getmtime(os.path.join(DL, f)))
                                         for k, f in PHOTOS.items() if os.path.exists(os.path.join(DL, f))}})
        m = re.fullmatch(r"/photo/(\w+)", path)
        if m and m.group(1) in PHOTOS:
            return self.file(os.path.join(DL, PHOTOS[m.group(1)]))
        m = re.fullmatch(r"/audit/([\w.]+\.jpg)", path)  # 검수 사진
        if m:
            return self.file(os.path.join(ms.HERE, "audit", m.group(1)))
        m = re.fullmatch(r"/tpl/(\w+)", path)  # 버섯 목록 썸네일
        if m:
            key = m.group(1)
            for p in (os.path.join(ms.MY_DIR, key + ".jpg"), os.path.join(ms.HERE, "templates", key + ".jpg")):
                if os.path.exists(p):
                    return self.file(p)
        self.send({"error": "없음"}, code=404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n) or b"{}")
        path = self.path
        if path == "/api/start":
            return self.send({"ok": start(data.get("target"), bool(data.get("bg", True)), data.get("opts", {}))})
        if path == "/api/stop":
            return self.send({"ok": stop()})
        if path == "/api/register_point":
            return self.send(register_point(data))
        if path == "/api/auto_register":
            return self.send(auto_register(data))
        if path == "/api/register":
            return self.send(register(data))
        if path == "/api/photos_clear":  # 확인용 사진 전부 지우기
            for f in PHOTOS.values():
                try:
                    os.remove(os.path.join(DL, f))
                except OSError:
                    pass
            return self.send({"ok": "사진을 지웠습니다"})
        if path == "/api/history_delete":
            return self.send(history_delete(str(data.get("lat", "")), str(data.get("lng", ""))))
        if path == "/api/delete":
            return self.send(delete_target(data.get("key", "")))
        if path == "/api/open_map":  # 구글 지도 앱으로 열기
            lat, lng = float(data["lat"]), float(data["lng"])
            sh(f"am start -a android.intent.action.VIEW -d 'https://www.google.com/maps/search/?api=1&query={lat},{lng}'")
            return self.send({"ok": "지도 앱을 열었습니다"})
        if path == "/api/share":  # 카톡 등으로 좌표 보내기 (안드로이드 공유 창)
            lat, lng = float(data["lat"]), float(data["lng"])
            text = f"{data.get('name', '버섯')} 인원 {data.get('people', '?')}명 {lat},{lng} https://www.google.com/maps/search/?api=1&query={lat},{lng}"
            sh("am start -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT " + shlex.quote(text))
            return self.send({"ok": "공유 창을 열었습니다"})
        if path == "/api/sound":  # 블룸 소리만 켜기/끄기
            on = bool(data.get("on"))
            ms.bloom_sound(on)
            if on:
                os.path.exists(SOUND_OFF) and os.remove(SOUND_OFF)
            else:
                open(SOUND_OFF, "w").close()
            return self.send({"ok": "블룸 소리를 켰어요" if on else "블룸 소리를 껐어요 (다른 앱 소리는 그대로)"})
        if path == "/api/gps_off":  # 원래 위치로
            ms.gps_clear()
            state["gps"] = None
            return self.send({"ok": "원래 위치로 돌아왔어요"})
        if path == "/api/cleanup":
            return self.send({"ok": f"임시 파일 {cleanup()}KB 정리했어요 (찾은 내역·검수 기록은 그대로)"})
        if path == "/api/reset":
            return self.send(reset_all())
        if path == "/api/check_update":  # 화면을 열 때(2분에 한 번까지)·버전 글자를 누를 때
            if data.get("force") or time.time() - state.get("update_checked", 0) > 120:
                threading.Thread(target=check_update, daemon=True).start()
            return self.send({"ok": "확인 중"})
        if path == "/api/update":
            return self.send(do_update())
        if path == "/api/quit":
            return self.send(quit_all())
        self.send({"error": "없음"}, code=404)


def old_server():
    """이미 켜진 앱 서버의 (응답함?, 버전, 찾는 중?). 예전 버전은 /api/version 이 없어 버전 None."""
    import urllib.request
    try:
        st = json.load(urllib.request.urlopen(URL + "/api/status", timeout=15))
    except Exception:
        return False, None, False
    try:
        ver = json.load(urllib.request.urlopen(URL + "/api/version", timeout=15)).get("version")
    except Exception:
        ver = None
    return True, ver, st.get("running", False)


def our_procs():
    """이 앱이 띄운 프로세스들 {pid: 'app'|'scan'} (/proc 에서 찾음)."""
    me, found = os.getpid(), {}
    for d in os.listdir("/proc"):
        if d.isdigit() and int(d) != me:
            try:
                cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ")
            except OSError:
                continue
            if b"scout_app.py" in cmd:
                found[int(d)] = "app"
            elif b"mushroom_scout.py" in cmd and b" scan" in cmd:
                found[int(d)] = "scan"
    return found


def kill_old_server():
    """업데이트 전 앱 서버와 그 서버가 돌리던 찾기 작업까지 정리 (찾기는 Ctrl+C 처럼 멈춰 가상 화면 등을 정리하게)."""
    for pid, kind in our_procs().items():
        try:
            os.kill(pid, signal.SIGINT if kind == "scan" else signal.SIGKILL)
        except OSError:
            pass
    for _ in range(30):  # 찾기 작업이 정리하고 끝날 때까지 (최대 30초)
        if not our_procs():
            break
        time.sleep(1)


def main():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:  # 이미 켜져 있음
        alive, ver, scanning = old_server()
        if not alive or ver == VERSION or scanning:  # 응답이 늦거나(찾는 중 바쁨) 같은 버전이면 절대 끄지 않고 화면만
            print("앱이 이미 켜져 있어서 화면만 엽니다." + (" (찾는 중이라 업데이트는 [중지] 후 다시 누르면 적용)" if ver != VERSION else ""))
            termux("termux-open-url", URL)
            return
        print("예전 버전 앱을 끄고 새 버전으로 다시 켭니다.")
        kill_old_server()
        for _ in range(20):  # 예전 서버가 포트를 놓을 때까지
            try:
                srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
                break
            except OSError:
                time.sleep(1)
        else:
            sys.exit("예전 앱이 안 꺼집니다. Termux 를 강제 중지한 뒤 [버섯찾기]를 다시 눌러 주세요.")
    cleanup(include_photos=False)  # 켤 때마다 쓰다 만 사진 등 찌꺼기 정리
    if os.path.isdir("/proc"):  # 앱 서버만 꺼지고 찾기 작업은 살아 있으면(위젯을 다시 누른 경우 등) 끄지 않고 이어받음
        for pid, kind in our_procs().items():
            if kind != "scan":
                continue
            if running():  # 둘 이상이면 하나만 남김
                os.kill(pid, signal.SIGINT)
                continue
            try:
                saved = json.load(open(SCAN_STATE, encoding="utf-8"))
            except (OSError, ValueError):
                saved = {}
            state.update(proc=Adopted(pid), target=saved.get("target"), bg=saved.get("bg", True), opts=saved.get("opts", {}),
                         started=saved.get("started"), gps=saved.get("gps"))
            print("찾던 작업이 아직 돌고 있어서 그대로 이어받았어요.")
    signal.signal(signal.SIGTERM, lambda *_: (stop(), os._exit(0)))
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, lambda *_: (stop(), os._exit(0)))
    threading.Thread(target=watch_shizuku, daemon=True).start()
    threading.Thread(target=watch_crash, daemon=True).start()
    threading.Thread(target=watch_update, daemon=True).start()
    threading.Thread(target=watch_gps, daemon=True).start()
    if os.environ.get("SCOUT_RESUME"):  # 업데이트 전에 찾던 것 이어서
        r = json.loads(os.environ.pop("SCOUT_RESUME"))
        threading.Timer(3, lambda: start(r["target"], r["bg"], r["opts"])).start()
    print(f"버섯 탐지기 앱: {URL}  (브라우저가 열립니다. 이 Termux 창은 닫지 마세요)")
    if "--no-open" not in sys.argv:
        termux("termux-open-url", URL)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop()


if __name__ == "__main__":
    main()
