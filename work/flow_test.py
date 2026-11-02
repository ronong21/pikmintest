import importlib.util, cv2, os, tempfile
spec = importlib.util.spec_from_file_location("m", "mushroom_scout.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
I = r'C:/Users/zeze/AppData/Local/Temp/claude/D--BX----2026----------pikmin/a7e2d1e1-a3d2-474c-93fa-d61a703782af/images/'
L = lambda p: cv2.imread(I + p)
T = m.load_templates()
# 1) 인원 배지
for p in ["13.webp", "6.webp", "5.webp"]:
    im = L(p)
    print(p, [(n, x, y, m.read_people(im, x, y)) for n, s, x, y in m.detect(im, T)])
# 2) 이름표 / 버튼
g = [h for h in m.detect(L("6.webp"), T) if h[0] == "fancy_giant"][0]
print("label on 7:", m.find_label(L("7.webp"), g[2], g[3]), " label on 6(none expected):", m.find_label(L("6.webp"), g[2], g[3]))
print("move_here on 10:", m.find_ui(L("10.webp"), "move_here"), " on 6:", m.find_ui(L("6.webp"), "move_here"))
print("open_map on 11:", m.find_ui(L("11.webp"), "open_map"), " on 10:", m.find_ui(L("10.webp"), "open_map"))
# 3) 전체 흐름 모의 실행: 지도(6) → 탭 후 이름표(7) → 상세(10) → 팝업(11) → 지도앱 → 복귀(6)
screens = iter([L("6.webp"), L("7.webp"), L("10.webp"), L("11.webp"), L("6.webp")])
calls = []
tmp = tempfile.mkdtemp(); m.UI_PATH = os.path.join(tmp, "ui.xml"); m.HERE = tmp
def fake_adb(*a, **k):
    calls.append(" ".join(a[1:4]))
    if "uiautomator" in a: open(m.UI_PATH, "w").write('<node text="37.712716,-122.455983"/>')
    return ""
m.adb = fake_adb; m.screencap = lambda: next(screens); m.time.sleep = lambda s: None
m.alert = lambda msg: print("ALERT:", msg); m.swipe = lambda s: calls.append("swipe")
m.scan(steps=1, want="fancy_giant", rounds=1, max_people=7)
print(calls)
print(open(os.path.join(tmp, "found.txt")).read().strip())
