# Termux 에서 한 번만 실행: bash ~/storage/downloads/termux_setup.sh
set -e
pkg update -y
pkg install -y python android-tools termux-api python-numpy
# 패키지 이름이 저장소 버전마다 달라서 순서대로 시도
pkg install -y opencv-python || pkg install -y python-opencv || { pkg install -y x11-repo && pkg install -y opencv-python; }
mkdir -p ~/scout/templates
cp ~/storage/downloads/mushroom_scout.py ~/scout/
cp ~/storage/downloads/templates/*.png ~/scout/templates/ 2>/dev/null || true
cd ~/scout && python mushroom_scout.py selftest
echo "설치 완료. 다음: adb pair / adb connect"
