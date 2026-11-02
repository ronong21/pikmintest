# scout_setup.sh(폰 설치/업데이트 묶음)를 만든다: python work/build_bundle.py
import base64, glob, os, textwrap, time

# 버전: 만들 때마다 번호 +1, 만든 시각과 함께 version.txt 로 묶음에 넣는다 (앱 제목 옆에 표시)
NO = "work/build_no.txt"
no = int(open(NO).read()) + 1 if os.path.exists(NO) else 1
open(NO, "w").write(str(no))
open("version.txt", "w", encoding="utf-8").write(f"v{no} · {time.strftime('%Y-%m-%d %H:%M')}")

read = lambda p: open(p, encoding="utf-8").read().rstrip("\n")
files = ["version.txt", "scout_display.dex", "scout_app.py", "app.html", "example_register.jpg"] + sorted(glob.glob("templates/*.jpg")) + sorted(glob.glob("templates/ui/*.png")) + sorted(glob.glob("templates/digits/*.png"))
out = ["set -e", "mkdir -p ~/scout/templates/ui ~/scout/templates/digits ~/.shortcuts",
       "rm -f ~/scout/templates/*.png ~/scout/templates/*.jpg",
       "cat > ~/scout/mushroom_scout.py <<'PYEOF'", read("mushroom_scout.py"), "PYEOF"]
for f in files:
    f = f.replace("\\", "/")
    out += [f"base64 -d > ~/scout/{f} <<'B64'", *textwrap.wrap(base64.b64encode(open(f, "rb").read()).decode(), 76), "B64"]
# 버튼 하나(메뉴)로 통합. 실행 중인 버튼 파일을 덮어쓰지 않게 새 파일로 바꿔치기(mv)
out += ["cat > ~/.shortcuts/.new <<'SHEOF'", read("work/shortcut.sh"), "SHEOF",
        "mv ~/.shortcuts/.new ~/.shortcuts/버섯찾기",
        "rm -f ~/.shortcuts/버섯내역 ~/.shortcuts/버섯찾기_백그라운드",
        "chmod 700 ~/.shortcuts ~/.shortcuts/버섯찾기",
        # 홈 화면 버튼이 Termux 창을 띄우려면 '다른 앱 위에 표시' 권한이 필요(안드로이드 10+). 절전 예외도 같이
        "timeout --foreground 15 ~/bin/rish -c 'appops set com.termux SYSTEM_ALERT_WINDOW allow; dumpsys deviceidle whitelist +com.termux' </dev/null >/dev/null 2>&1 "
        "&& echo '홈 화면 버튼 권한 OK' || echo '※ Shizuku 가 꺼져 있어 버튼 권한을 못 켰습니다. Shizuku [시작] 후 다시 실행하세요'",
        "cd ~/scout && python mushroom_scout.py selftest && echo '설치/업데이트 완료'"]
open("scout_setup.sh", "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
print(len(files), "files", open("version.txt", encoding="utf-8").read())
