# DiagAuthor

Python · PySide6(Qt Widgets) · SQLite로 작성한 Windows 오프라인 ECU 진단 데이터 편집기의 첫 개발 빌드입니다. 실제 ECU 통신·플래싱은 수행하지 않습니다. Rust는 도입하지 않았습니다.

실행 파일은 `dist/DiagAuthor.dist/DiagAuthor.exe`입니다. `DiagAuthor.dist` 폴더 전체를 함께 보관하세요. 다른 PC에 전달할 때는 `dist/DiagAuthor-0.1.0-win64.zip`을 풀어서 사용합니다.

현재 구현 범위와 미완료 항목은 [빌드 안내](BUILD-NOTES.md)와 [구현 현황](docs/15-implementation-status.md)에 기록합니다. 자동 테스트는 CDD·도메인·저장·마이그레이션·복구·명령·Qt 화면 동작을 포함합니다. 전체 출시 인수 조건은 아직 충족하지 않았습니다.

## 기본 사용 흐름

1. **새 프로젝트**에서 ECU 이름·Qualifier·언어를 입력합니다.
2. 탐색 트리의 **데이터 타입**에서 정수 길이·바이트 순서·변환식을 정의합니다.
3. 변형 아래 **DID**에서 번호와 데이터 구조를 추가합니다. 읽기 서비스 자동 생성을 선택할 수 있습니다.
4. **서비스**에서 요청·정응답 payload, NRC, 세션·보안 상태 조건을 편집합니다. **전문 미리보기**로 값을 입력할 수 있습니다.
5. **DTC**는 직접 추가하거나 CSV 열 매핑 후 선택한 정상 행을 일괄 등록합니다.
6. CDD는 도구 모음의 **CDD·CDDT 가져오기** 또는 **열기**에서 `.cdd`를 선택해 새 프로젝트로 가져옵니다. `.cddt`(문서 템플릿)를 선택하면 템플릿을 바탕으로 한 새 프로젝트를 만듭니다. 해석하지 못한 항목은 **미지원 항목**에 원문으로 남습니다.
7. **Ctrl+S**로 `.diagproj`를 저장합니다. 편집은 별도의 작업 DB에 반영되며 저장하지 않고 닫으면 기존 프로젝트 파일을 유지합니다.

**Ctrl+Z/Y** 실행 취소·다시 실행, **Ctrl+F** 검색, **F2** 편집, **F5** 앱 내 프로젝트 검증 기능을 제공합니다.

탐색 트리·목록의 우클릭 메뉴에서 경로 복사와 관련 객체 이동을 사용할 수 있습니다. `Ctrl+L`은 `diag://` 객체 경로 이동입니다. **프로젝트** 메뉴에는 Qualifier·Shortcut Qualifier·Attribute 개요, 변형 Compact View·Compare, 저장된 프로젝트와 비교, 상태 그래프, 번역 표가 있습니다. 변형 Compare는 같은 프로젝트의 두 변형을 대상으로 합니다. 파일 비교는 현재 작업본과 선택한 `.diagproj`의 DID·DTC 정의와 인스턴스·서비스(헤더·NRC·전문·상태 조건/전이·원본 변형)를 비교합니다.

설계 문서의 온도 예제를 입력하려면 데이터 타입을 `unsigned`, 16비트, `big`, `linear`, 계수 `0.1`, 오프셋 `-40`, 단위 `°C`로 설정합니다. DID `0x1234`의 데이터 구조에 이 타입의 필드를 추가하고 기본 원시값을 `650`으로 입력합니다. 이 예제의 명세상 정응답은 `62 12 34 02 8A`, 물리값은 `25.0`입니다. 이는 사용 예제의 기대값이며 이번 빌드에서 실행해 확인한 결과는 아닙니다.

객체의 우클릭 **참조 영향**에서 중첩 타입·DID·서비스·변형 사용처를 확인할 수 있습니다. 데이터 타입 편집 시 공유 영향 목록을 확인하고, **편집 → 데이터 타입 독립 복사**로 하위 타입·구조까지 복사할 수 있습니다. 사본은 사용할 DID·서비스의 데이터 구조에서 직접 선택합니다. 삭제는 사전 검사에 통과한 경우에만 적용 버튼을 표시합니다.

오른쪽 패널의 **데이터 배치** 탭은 DID·서비스의 멤버 표와 바이트 격자를, **세부 표** 탭은 상태 그룹의 상태 같은 하위 목록을 보여 줍니다. 진단 인스턴스 노드는 템플릿 서비스 행과 요청·응답 패턴(`22 F1 90` / `62 F1 90 zz` / `7F 22 rc`)을 보여 줍니다. 여러 변형이 쓰는 DID·서비스를 편집할 때는 현재 변형만 사본으로 바꿀지, 참조하는 모든 변형을 고칠지 고릅니다. 이 화면들은 22차에 추가했고 아직 테스트를 실행하지 않았습니다([빌드 안내](BUILD-NOTES.md) 22차).

## 소스에서 실행·빌드

Python 3.13 x64와 uv가 필요합니다. 선택적인 Nuitka 빌드에는 Visual Studio 2022 C++ 도구와 Windows 10/11 SDK가 추가로 필요합니다.

```powershell
.\build.ps1 -InstallDependencies
```

의존성을 준비한 후 다시 빌드할 때는 `./build.ps1`을 사용합니다. 개발 실행 명령은 `.\.venv\Scripts\python.exe main.py`입니다. 빌드 스크립트는 테스트나 앱 실행을 호출하지 않습니다.

이번 배포는 [PyInstaller](https://pyinstaller.org/en/stable/usage.html)의 폴더 방식으로 만듭니다. 설치된 Visual Studio에 Windows SDK가 없어 Nuitka의 C 컴파일이 중단되어 선택한 대체 방식입니다. [Qt 공식 pyside6-deploy](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html) 설정도 유지하며, SDK를 갖춘 환경에서는 `.\build.ps1 -Backend Nuitka`로 사용할 수 있습니다. 프로젝트 저장은 [Python sqlite3 백업 API](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)를 이용합니다.

## 코드 구성

| 파일 | 책임 |
|---|---|
| `diagstudio/ui.py`, `dialogs.py` | 한국어 주 화면, 표 모델, 속성·CSV·출력 대화상자 |
| `diagstudio/overviews.py` | Qualifier·속성·번역·변형 비교 표, 상태 그래프 |
| `diagstudio/comparison.py` | 저장된 `.diagproj`와 읽기 전용 비교(DID·DTC·인스턴스·서비스, UUID 무관) |
| `diagstudio/references.py` | 중첩 타입·구조·DID·고장 메모리·서비스·상태·변형의 역참조 영향 그래프 |
| `diagstudio/presentation.py` | 속성 창 텍스트(전문 배치·변환식) 생성, Qt 비의존 |
| `diagstudio/layout_view.py` | 데이터 배치 위젯: 멤버 표·바이트 격자, 서비스 요청·응답·NR 탭 |
| `diagstudio/pages.py` | 작업 기록, 세부 표, Shortcut 행렬, Compact View, 고장 메모리 레코드, Rework, 상태 조건 일괄 변경 화면 |
| `diagstudio/source_views.py` | 원본 CDD 보존 원문의 읽기 전용 표(인터페이스·식별 패턴·템플릿 클래스·Defaults·이력) 해석, Qt 비의존 |
| `diagstudio/workers.py` | GUI와 분리한 단일 작업 큐·DB 소유 스레드 |
| `diagstudio/application.py` | 프로젝트 사용 사례·편집 명령·변형 분리 |
| `diagstudio/domain.py` | 정확한 숫자 변환·전문 배치·도메인 검증 |
| `diagstudio/persistence.py`, `schema.sql` | 정규화 SQLite·행 변경 이력·저장·복구 |
| `diagstudio/interchange.py` | CDD 타입·DID 가져오기·CSV·ODX 출력 어댑터 |
| `diagstudio/cdd_model.py` | CDD 템플릿 참조 해석: 상태·서비스·인스턴스·DTC·사용자 속성 |
| `diagstudio/migrations.py` | 프로젝트 스키마 마이그레이션(1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10) |
| `build.ps1`, `DiagAuthor.spec`, `pysidedeploy.spec` | Windows standalone 빌드 |
| `test/` | CDD 가져오기·표시·저장 회귀 테스트 (`python -m unittest discover -s test -v`) |

[전체 설계 문서](docs/README.md)는 최종 목표 명세입니다. 현재 구현 상태는 [BUILD-NOTES.md](BUILD-NOTES.md)가 기준입니다.
