# 에이전트 안내

- **먼저 `README.md`를 읽는다.** 현재 상태, 사용법, 배포, 함정, 남은 일이 모두 정리되어 있다.
- 사용자와는 **한국어**로, 초보자도 따라 할 수 있게 단계별로 이야기한다. 폰에서 입력할 명령은 한 줄씩 복사할 수 있게 준다.
- 고친 뒤 폰에 내보내는 순서:
  ```
  python mushroom_scout.py selftest
  python work/build_bundle.py
  gh gist edit 30919049d2c3c99b192e806267b0a481 -f scout_setup.sh scout_setup.sh
  ```
  폰 앱 화면에 [지금 업데이트]가 뜨고, 제목 옆 버전 번호가 올라간다.
- `work/vd/ScoutDisplay.java`를 고쳤다면 README의 빌드 명령으로 `scout_display.dex`를 다시 만든다. JDK와 Android SDK 35 build-tools가 필요하다.
- 실기기는 PC와 연결되지 않는다. 회사 정책으로 USB ADB가 막혀 있다. 그래서 폰 동작은 사용자가 앱 화면의 진행 기록·확인용 사진을 캡처해서 보내 주는 것으로 확인한다.
- 게임 서버나 메모리는 건드리지 않는다. 화면 인식과 화면 터치만 쓴다.
