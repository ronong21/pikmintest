#!/data/data/com.termux/files/usr/bin/bash
# 홈 화면 Termux:Widget 버튼 하나: 최신 버전 받기 → Shizuku 확인 → (처음 한 번) 알림 확인 → 메뉴
URL="https://gist.githubusercontent.com/ronong21/30919049d2c3c99b192e806267b0a481/raw/scout_setup.sh"
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
while true; do
  echo
  echo "===== 버섯 탐지기 ====="
  echo " 1) 거대 화려한 버섯 찾기 - 블룸 화면 보면서 (블룸에서 나가면 멈춤)"
  echo " 2) (백그라운드) 거대 화려한 버섯 찾기 - 다른 앱 쓰거나 화면 꺼도 됨"
  echo " 3) (백그라운드) 중형 화려한 버섯 찾기 - 평일용 (거대는 주말에만 나옴)"
  echo " 4) 찾은 내역 (번호 고르면 지도로 열기)"
  echo " 5) 확인용 사진 보기 (안 보이는 가상 화면, 이름표 못 찾은 순간 등)"
  echo " 9) 모두 끄기 (블룸·탐지·Termux 끄기. Shizuku 는 재부팅 전까지 켜 둠)"
  echo " 0) 닫기"
  echo "----------------------"
  echo " 사용법: 번호를 누르고 [엔터]"
  echo " 찾는 중 멈추기: 이 창에서 [엔터] → 이 메뉴로 돌아옴 (Termux 알림을 누르면 이 창이 열림)"
  echo " 내역·사진에서: 번호 + [엔터] = 열기, 그냥 [엔터] = 메뉴로"
  read -p "번호: " n
  case "$n" in
    9) echo "모두 끄는 중... (Shizuku 는 켜 둡니다: 다시 켜려면 와이파이가 필요해서)"
       sh_ "pkill -f 'Scout[D]isplay'; dumpsys deviceidle enable; am force-stop com.nianticlabs.pikmin" >/dev/null 2>&1
       termux-wake-unlock
       # Termux 가 꺼져도 끝까지 실행되게 셸 쪽에서 1초 뒤 처리
       sh_ "nohup sh -c 'sleep 1; am force-stop com.termux' >/dev/null 2>&1 &" >/dev/null 2>&1
       sleep 5
       exit 0 ;;
    1|2|3) echo
         if [ "$n" = 1 ]; then
           echo "[주의] 블룸을 '광역 지도' 화면으로 맞춰 두세요. 찾는 동안 블룸에서 나가면 멈춥니다."
         else
           echo "[주의] 블룸이 폰 화면에서 사라집니다(안 보이는 가상 화면으로 이동, 정상)."
           echo "  - 찾는 동안 블룸 앱을 직접 열지 마세요 (서로 뺏고 뺏겨서 엉킵니다)."
           echo "  - 블룸을 하고 싶으면: Termux 알림 눌러 이 창 열기 → 엔터로 멈춤 → 그 뒤에 블룸 열기"
           echo "  - 지금 가상 화면 모습: 메뉴 5번 (칸마다 갱신)"
         fi
         echo "  - 블룸 첫 화면의 안내 팝업 → 나침반 → 광역 지도는 알아서 눌러 들어갑니다."
         echo "  - 인원 4명 이하(배지 없음 포함)만 알립니다."
         echo "  - 찾으면 알림이 오고, 이 메뉴 4번(찾은 내역)에 쌓입니다. 충전기 연결 권장."
         echo
         termux-wake-lock
         want=fancy_giant; [ "$n" = 3 ] && want=fancy_mid
         python mushroom_scout.py scan --want $want $([ "$n" != 1 ] && echo --bg)
         termux-wake-unlock ;;
    4) python mushroom_scout.py history ;;
    5) D=/storage/emulated/0/Download; i=0; files=()
       for f in scout_now.jpg scout_nolabel.jpg scout_stuck.jpg; do
         [ -f "$D/$f" ] || continue
         i=$((i+1)); files+=("$D/$f")
         case $f in scout_now.jpg) d="지금(마지막) 가상 화면";; scout_nolabel.jpg) d="이름표를 못 찾은 순간";; *) d="지도가 멈춘 순간";; esac
         echo " $i) $d  ($(date -r "$D/$f" '+%m-%d %H:%M'))"
       done
       if [ $i = 0 ]; then echo "아직 사진이 없습니다."; else
         read -p "열 번호 (그냥 엔터 = 메뉴로): " k
         [ -n "$k" ] && [ "$k" -ge 1 ] 2>/dev/null && [ "$k" -le $i ] &&            sh_ "am start -a android.intent.action.VIEW -t image/jpeg -d file://${files[$((k-1))]}" >/dev/null 2>&1  # Termux 경유로 열면 갤러리가 회색으로 못 읽음 → 셸에서 파일로 직접 열기
       fi ;;
    *) exit 0 ;;
  esac
done
