#!/data/data/com.termux/files/usr/bin/bash
# 홈 화면 Termux:Widget 버튼: 최신 버전 받기 → Shizuku 확인 → 피크민 블룸 띄우고 탐지
URL="https://gist.githubusercontent.com/ronong21/30919049d2c3c99b192e806267b0a481/raw/scout_setup.sh"
echo "최신 버전 확인 중..."
if curl -sfL "$URL?$(date +%s)" -o ~/.scout_setup.sh; then bash ~/.scout_setup.sh | tail -1; else echo "업데이트 실패(인터넷?) - 지금 버전으로 실행"; fi
until timeout 5 ~/bin/rish -c true >/dev/null 2>&1; do
  echo "Shizuku 가 꺼져 있어요. 열리는 Shizuku 앱에서 [시작]을 누르면 자동으로 이어집니다."
  am start -n moe.shizuku.privileged.api/moe.shizuku.manager.MainActivity >/dev/null 2>&1
  sleep 8
done
termux-wake-lock
cd ~/scout && python mushroom_scout.py scan --want fancy_giant
echo "끝났습니다. 아무 키나 누르면 닫힙니다."; read -n 1
