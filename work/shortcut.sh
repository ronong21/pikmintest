#!/data/data/com.termux/files/usr/bin/bash
# 홈 화면 Termux:Widget 버튼 하나: 최신 버전 받기 → Shizuku 확인 → (처음 한 번) 알림 확인 → 앱 화면(브라우저)
URL="https://gist.githubusercontent.com/ronong21/30919049d2c3c99b192e806267b0a481/raw/scout_setup.sh"
# 앱이 이미 켜져 있으면(찾는 중 포함) 업데이트·재시작 없이 화면만 다시 연다. 업데이트는 [모두 끄기] 후 다시 누를 때
if [ -z "$SCOUT_UPDATED" ] && curl -s -m 15 http://127.0.0.1:8765/api/version >/dev/null 2>&1; then
  echo "버섯 탐지기가 이미 켜져 있어서 화면만 엽니다."
  termux-open-url http://127.0.0.1:8765
  exit 0
fi
if [ -z "$SCOUT_UPDATED" ]; then
  echo "최신 버전 확인 중..."
  if curl -sfL "$URL?$(date +%s)" -o ~/.scout_setup.sh; then
    bash ~/.scout_setup.sh </dev/null | tail -1
    # 지금 도는 건 업데이트 전 버튼이라, 방금 받은 새 버튼으로 다시 시작
    SCOUT_UPDATED=1 exec bash ~/.shortcuts/버섯찾기 "$@"
  else echo "업데이트 실패(인터넷?) - 지금 버전으로 실행"; fi
fi
# rish 는 입력(</dev/null)을 막아 줘야 timeout 안에서 멈추지 않는다
sh_() { timeout --foreground 15 ~/bin/rish -c "$1" </dev/null; }
if ! sh_ true >/dev/null 2>&1; then
  echo "Shizuku 연결 안 됨. Shizuku 앱에서 '실행 중'이 아니면 [시작]을 누르고, Termux 로 돌아오세요."
  am start -n moe.shizuku.privileged.api/moe.shizuku.manager.MainActivity >/dev/null 2>&1
  until sh_ true >/dev/null 2>&1; do sleep 3; done
  echo "Shizuku 연결됨"
fi
cd ~/scout
# 알림은 Shizuku(셸)가 보내므로 'Shell' 앱 알림이 켜져 있어야 한다. 확인되면 다음부터 건너뜀
if [ ! -f .notify_ok ]; then
  sh_ "pm grant com.android.shell android.permission.POST_NOTIFICATIONS; appops set com.android.shell POST_NOTIFICATION allow" >/dev/null 2>&1
  sh_ "cmd notification post -t '버섯 탐지기' scouttest '알림 테스트입니다'" >/dev/null 2>&1
  read -p "방금 '알림 테스트' 알림이 왔나요? (y/n) " a
  if [ "$a" != y ]; then
    echo "열리는 화면에서 알림을 [허용]으로 켜고, Termux 로 돌아와 엔터를 누르세요."
    sh_ "am start -a android.settings.APP_NOTIFICATION_SETTINGS --es android.provider.extra.APP_PACKAGE com.android.shell" >/dev/null 2>&1
    read -p "켰으면 엔터 "
  fi
  touch .notify_ok
fi
echo
echo "버섯 탐지기 앱 화면을 브라우저로 엽니다. (안 열리면 브라우저 주소창에 127.0.0.1:8765)"
echo "※ 이 Termux 창을 '닫기(Exit)'하면 앱이 꺼집니다. 홈 버튼으로 나가는 건 괜찮아요."
python scout_app.py
echo "앱이 꺼졌습니다. 아무 키나 누르면 닫힙니다."; read -n 1
