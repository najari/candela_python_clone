# DiagAuthor 작업 지침

## 프로젝트 규칙 요약

- 현재 구현 범위와 남은 과제의 기준은 `BUILD-NOTES.md`, 목표 명세는 `docs/`.
- CDD·ODX 의미는 설치 예제(`C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples`)와 그 `Repository/ODXEnvelope`의 Vector 출력으로 증명한 것만 매핑한다. 증명하지 못한 의미는 원문을 보존하고 출력을 차단한다. 조용히 버리거나 반올림하지 않는다.
- Vector 예제·도움말 파일은 저장소에 복사하지 않는다.
- 테스트: `.venv\Scripts\python.exe -m unittest discover -s test -v`
- Windows 확인은 콘솔 창에서 진행이 보이는 스크립트로 한다: 테스트만 `build\run_tests.cmd`, 전체 `build\run_checks.cmd`(테스트 → 빌드 → GUI 스모크), GUI만 `build\run_gui_smoke.cmd`. 결과는 `build\checks\`.
- 빌드 전에 실행 중인 DiagAuthor를 닫는다. 켜져 있으면 `dist\DiagAuthor.dist` 교체가 파일 잠금으로 실패하고, 스모크가 이전 EXE를 검사하게 된다.
- 테스트에서 sqlite 연결은 `contextlib.closing`으로 닫는다. `with sqlite3.connect()`는 닫지 않아 Windows에서 파일 교체가 거부된다.
- 스키마를 바꾸면 `diagstudio/migrations.py`에 이전 버전 변환 스크립트를 추가하고 `schema.sql`과 같은 결과가 되게 한다.
