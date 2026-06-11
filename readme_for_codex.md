# 기능 이식용 메모

이 문서는 이 파일이 들어 있는 프로젝트 루트 기준이다. 다른 개발자가 작업 중인 별도 버전에 기능을 이식할 때, 다음 Codex가 참고하도록 작성했다. 프로그램 내부 파일/폴더 경로만 언급하고, 특정 컴퓨터의 상위 폴더명이나 비교 대상 프로젝트 폴더명은 사용하지 않는다.

문서 인코딩은 UTF-8 without BOM 기준이다. PowerShell 5.1 같은 환경에서 `Get-Content` 출력이 깨져 보일 수 있으나, 파일 자체는 UTF-8로 저장한다.

## 현재 버전에서 추가/개선된 핵심 기능

1. Chrome/Edge 확장 프로그램을 통한 브라우저 편집기 텍스트 인식
2. 브라우저 편집기 원본 반영 경로
3. OpenAI API 기반 맞춤법 교정
4. 맞춤법 교정 결과 구조화: 교정문, 전체 피드백, 개별 오류 정보
5. 실시간 모드에서 AI 요청 프리징 완화: 비동기 워커/디바운스
6. DPI/디스플레이 배율 대응
7. 로그 수집 경로 추가
8. 앱 설정 저장: `user_settings.json`

이식 우선순위는 `브라우저 확장 + 브리지 + 실시간 입력 연결`이 가장 높다. 네이버 카페와 Gmail에서 정상 동작 확인됨.

## 포팅 우선 파일 목록

그대로 가져올 가능성이 높은 파일:

- `browser_extension/manifest.json`
- `browser_extension/background.js`
- `browser_extension/content.js`
- `browser_extension/popup.html`
- `browser_extension/popup.js`
- `browser_extension/README.md`
- `client/input/browser_extension_bridge.py`
- `client/core/line_structure.py`
- `client/ui/dpi.py`
- `client/core/ai_client.py`
- `client/core/analyzer.py`
- `server/ai_service.py`
- `server/ai_cache.py`

기존 버전 코드와 연결해야 하는 파일:

- `client/input/realtime_text_monitor.py`
- `client/input/output_applier.py`
- `client/ui/main_window.py`
- `client/ui/result_panel.py`
- `client/ui/spelling_inspection_overlay.py`
- `client/core/local_server.py`
- `main.py`
- `client/main.py`
- `server/main.py`
- `server/schemas.py`

포팅하면 안 되거나 주의해야 하는 파일:

- `server/.env`: 실제 API 키가 있을 수 있으므로 복사 금지.
- `server/app.db`: 로컬 DB. 기능 이식 대상 아님.
- `.logs/*`: 실행 로그. 참고용으로만 사용.
- `__pycache__/*`: 복사 불필요.
- `user_settings.json`: 사용자 환경값. 기본값 참고용으로만 사용.

## 브라우저 확장 프로그램 구조

확장 폴더: `browser_extension/`

구성:

- `manifest.json`
  - Manifest V3
  - `content.js`를 `<all_urls>`와 `all_frames`에 삽입
  - 권한은 `activeTab`
  - 로컬 앱 접근은 `host_permissions: ["http://127.0.0.1:8766/*"]`
- `content.js`
  - 현재 포커스된 편집기를 찾음.
  - `textarea`, 일반 텍스트 `input`, `contenteditable`, open Shadow DOM 일부를 지원.
  - 사이트 DOM에서 직접 로컬 서버로 요청하지 않고, `chrome.runtime.sendMessage`로 background worker에 전달.
  - 원본 반영 명령을 주기적으로 폴링해서 편집기에 적용.
- `background.js`
  - content script와 로컬 브리지 사이의 fetch 중계.
  - 사이트가 직접 `127.0.0.1`에 접근하지 않으므로 Chrome의 "이 기기의 다른 앱 및 서비스 접근" 권한 팝업을 피하는 목적.
- `popup.html`, `popup.js`
  - 로컬 브리지 연결 여부, 현재 탭, 캡처 상태, 추출 방식, 줄바꿈/빈 줄 수를 표시.
  - 테스트 중 문제가 생기면 팝업의 `strategy`, `현재/전송 줄바꿈`, `빈줄` 숫자가 중요하다.

### 브라우저 텍스트 인식 흐름

1. 사용자가 브라우저 편집기에 포커스하고 입력한다.
2. `content.js`가 이벤트를 감지한다.
   - `selectionchange`
   - `beforeinput`
   - `input`
   - `paste`
   - `keyup`
   - `mouseup`
   - `focusin`
3. `content.js`가 현재 편집기 텍스트를 추출한다.
4. `content.js`가 background worker에 `writingAssistantBridgeCapture` 메시지를 보낸다.
5. `background.js`가 `http://127.0.0.1:8766/capture`로 POST한다.
6. `client/input/browser_extension_bridge.py`가 최신 이벤트를 메모리에 저장한다.
7. `client/input/realtime_text_monitor.py`가 브리지 이벤트를 우선 polling한다.
8. `client/ui/main_window.py`가 입력 텍스트와 `OutputTarget(mode="browser_extension")`를 저장한다.

중요: 브라우저 확장 브리지는 포트 `8766`을 사용한다. OpenAI/FastAPI 서버는 `8765`를 사용한다. 두 포트를 혼동하지 말 것.

## 사이트별/편집기별 텍스트 추출 전략

`browser_extension/content.js`의 핵심은 `extractEditableText(element)`이다.

일반 contenteditable:

- `generic-block`
- `inline`
- `dom-order`

위 후보를 만들고 구조 보존 점수로 하나를 선택한다.

Gmail:

- 반드시 `gmail-lines` 전략을 유지할 것.
- 최근 테스트 결과, Gmail에서 후보 점수 방식은 좋지 않았다.
- `innerText` 또는 "줄바꿈이 많은 후보"를 선택하는 방식은 시간이 지나며 줄바꿈이 사라지거나 반대로 빈 줄이 과해지는 문제가 있었다.
- 현재 방식은 Gmail 편집기 루트의 직계 줄 요소를 순서대로 읽고, 실제 빈 줄 DOM만 빈 줄로 인정한다.

검증된 사이트:

- 네이버 카페 글쓰기: 정상 작동 확인.
- Gmail 메일 작성: 정상 작동 확인.

주의:

- Google Docs 같은 canvas/가상 편집기는 현재 범위 밖이다.
- closed Shadow DOM은 접근 불가.
- cross-origin iframe은 Chrome이 content script 삽입을 허용하는 경우만 가능.

## 브라우저 원본 반영 흐름

원본 반영은 `OutputApplier`가 직접 브라우저 DOM을 건드리지 않는다.

흐름:

1. UI에서 원본 수정 버튼 클릭.
2. `client/input/output_applier.py`
   - `target.mode == "browser_extension"`이면 `_apply_to_browser_extension()`.
   - `browser_session_id`를 꺼내 브리지에 명령을 큐잉.
3. `client/input/browser_extension_bridge.py`
   - `queue_apply(session_id, text, style_info)`로 pending command 저장.
4. `browser_extension/content.js`
   - 주기적으로 `/command?session_id=...`를 polling.
   - 명령이 있으면 현재 편집기에 적용.

적용 방식:

- `input`, `textarea`: `setRangeText()` 계열 사용.
- 일반 `contenteditable`: DOM Range replacement.
- 네이버 스마트에디터 계열: `content.js` 안에 전용 적용 경로가 있다. 로그 메서드명은 `naver_smart_editor_blocks`.

주의:

- 브라우저 원본 반영은 현재 포커스/세션이 중요하다.
- 확장 프로그램이 캡처한 editable field가 없으면 원본 반영 불가.
- 브라우저 탭을 새로고침하면 session이 바뀔 수 있다.
- Gmail 원본 반영은 텍스트 인식보다 더 민감할 수 있으므로 이식 후 별도 테스트 필요.

## 브라우저 브리지

파일: `client/input/browser_extension_bridge.py`

역할:

- 로컬 HTTP 서버 실행: `127.0.0.1:8766`
- `/health`: 확장 팝업에서 연결 상태 확인
- `/capture`: 확장이 보낸 텍스트 수신
- `/command`: content script가 원본 반영 명령 polling
- `/applied`: content script가 적용 결과 보고

중요 로직:

- `record_capture(payload)`
  - `target_kind`가 `contenteditable` 또는 `textarea`인 경우만 수락.
  - 빈 텍스트는 무시.
  - 중복 signature는 무시.
  - 줄바꿈만 갑자기 많이 붕괴한 캡처는 거부한다.
- `_restore_session_blank_lines()`
  - 같은 session에서 내용 라인 수가 같고 빈 줄만 사라진 경우, 이전 캡처의 빈 줄 구조를 복원.
- `_is_collapsed_structure_regression()`
  - compact text가 같은데 줄바꿈 수만 크게 줄어든 경우 거부.

로그:

- `.logs/browser_extension_bridge.log`
- 캡처 시 `strategy`, `newlines`, `blank_lines`, `dom_debug` 후보 요약을 남긴다.

## 실시간 입력 모니터 연결

파일: `client/input/realtime_text_monitor.py`

핵심:

- 앱 시작 시 `get_browser_extension_bridge().start()`를 호출한다.
- 루프에서 브라우저 확장 이벤트를 먼저 확인한다.
- foreground가 브라우저라면 최근 브리지 이벤트를 잠시 재사용한다.
- 브라우저 확장 이벤트가 없으면 기존 `UniversalActiveTextReader`로 fallback한다.

주의:

- 브라우저 텍스트가 확장에서 들어왔으면 UIA 기반 브라우저 읽기보다 확장 이벤트를 우선해야 한다.
- 브라우저 포커스 전환만으로 불필요한 AI 교정이 실행되지 않도록 `main_window.py` 쪽에서 브라우저/확장 입력에 대한 자동 AI 실행을 조심해야 한다.

## OpenAI 맞춤법 교정

서버 파일:

- `server/ai_service.py`
- `server/ai_cache.py`
- `server/main.py`
- `server/schemas.py`

클라이언트 파일:

- `client/core/ai_client.py`
- `client/core/analyzer.py`

서버 실행:

- `client/core/local_server.py`가 `uvicorn main:app --host 127.0.0.1 --port 8765`로 서버를 띄운다.
- `AIClient` 기본 주소는 `http://127.0.0.1:8765`.

API:

- `POST /correct-public`
  - request: `{ "text": "..." }`
  - response:
    - `corrected_text: str`
    - `spelling_feedback: str | None`
    - `corrections: list[CorrectionIssue]`
- `POST /correct`
  - 로그인 사용자용. UsageLog 기록 포함.

OpenAI 설정:

- `server/.env` 또는 환경변수 필요.
- 필수: `OPENAI_API_KEY`
- 모델 선택:
  - `OPENAI_CORRECTION_MODEL`
  - 없으면 `OPENAI_MODEL`
  - 없으면 `AIService.DEFAULT_MODELS["correction"]`
- 현재 기본:
  - correction: `gpt-5-mini`
  - summary/evaluation/title/tone: `gpt-5-nano`
- 입력 제한/출력 제한:
  - `OPENAI_MAX_INPUT_CHARS`
  - `OPENAI_MAX_OUTPUT_TOKENS`
- 캐시:
  - `OPENAI_CACHE_ENABLED`
  - `OPENAI_CACHE_MAX_ENTRIES`
  - 캐시 파일: `.logs/ai_response_cache.json`
- 이벤트 로그:
  - `.logs/ai_events.jsonl`

맞춤법 응답 구조:

- OpenAI Responses API 사용.
- JSON schema 강제 시도.
- 실패 시 `json_object` 형식 fallback.
- 결과는 `corrected_text`, `feedback`, `corrections`로 정규화된다.
- `corrections` 항목에는 가능한 경우 원문 위치 `source_start`, `source_end`가 추가된다.

주의:

- `server/.env`는 절대 다른 repo에 그대로 복사하지 말 것.
- 일부 Python 파일의 한글 UI 문자열은 인코딩이 깨져 있다. 기능 이식 시 문자열은 새 버전 쪽에서 정상 한국어로 다시 작성하는 것이 안전하다.
- 현재 `server/ai_service.py`에서 summary/evaluation/title/tone은 `NotImplementedError` 상태다. 맞춤법 교정만 실사용 수준으로 가져오면 된다.

## 맞춤법 실행 방식과 프리징 방지

파일: `client/ui/main_window.py`

핵심 함수:

- `run_spell_check(blocking=False, debounce_ms=1500)`
- `_start_spell_check_worker()`
- `_run_spell_check_worker()`
- `handle_spell_check_result()`

동작:

- 기본 디바운스는 1500ms.
- AI 요청은 UI thread에서 직접 기다리지 않고 worker thread에서 실행한다.
- 요청 id와 source text를 저장해서, 오래된 응답이 뒤늦게 와도 현재 결과를 덮어쓰지 않게 한다.
- 브라우저/브라우저 확장 입력에서는 자동 AI 요청을 특히 조심해야 한다. 사용자가 타이핑 중이거나 포커스만 바뀌었을 때 AI가 돌면 비용과 프리징 문제가 생긴다.

교정 결과 추출:

- `_extract_corrected_text()`가 결과 박스 문자열에서 실제 교정문을 꺼낸다.
- AI 응답 JSON 자체가 UI에 그대로 보이지 않도록 `client/core/analyzer.py`에서 포맷한다.

## 맞춤법 학습/가이드 오버레이

파일: `client/ui/spelling_inspection_overlay.py`

목적:

- AI가 반환한 개별 교정 항목을 기반으로 화면 위에 안내 표시를 띄우는 구조.
- Word/Notepad/HWP/browser/browser_extension 같은 target mode를 구분.
- 위치 계산 실패나 매칭 실패를 로그로 남긴다.

주의:

- 브라우저 확장 입력에서는 텍스트 캡처와 원본 반영은 검증됐지만, 실제 웹페이지 위에 정확히 밑줄/마커를 그리는 것은 별도 검증이 필요하다.
- Word/HWP 좌표 계산은 환경 의존성이 크다.

## DPI/디스플레이 배율 대응

파일:

- `client/ui/dpi.py`
- `main.py`
- `client/main.py`

동작:

- PyQt 앱 생성 전에 `configure_high_dpi()` 호출.
- 환경변수:
  - `QT_ENABLE_HIGHDPI_SCALING=1`
  - `QT_AUTO_SCREEN_SCALE_FACTOR=1`
  - `QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough`
- Windows DPI awareness:
  - `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` 시도
  - 실패 시 `SetProcessDpiAwareness(2)`
  - 실패 시 `SetProcessDPIAware()`
- PyQt 속성:
  - `AA_EnableHighDpiScaling`
  - `AA_UseHighDpiPixmaps`
  - `HighDpiScaleFactorRoundingPolicy.PassThrough`

주의:

- 반드시 QApplication 생성 전에 호출해야 한다.
- 서로 다른 DPI 모니터 간 이동 문제는 완전히 사라지는 영역이 아니라, Qt/Windows 조합에 따라 추가 조정이 필요할 수 있다.

## 설정 저장

파일:

- `client/app_settings.py`
- `user_settings.json`

현재 설정 키:

- `default_dark_mode`
- `input_mode`
- `replace_mode`
- `history_enabled`

주의:

- `user_settings.json`은 사용자별 상태다. 기본값은 코드에 두고, 파일은 참고용으로만 보라.

## 로그 파일

주요 로그:

- `.logs/browser_extension_bridge.log`
- `.logs/realtime_monitor_errors.log`
- `.logs/ai_events.jsonl`
- `.logs/ai_response_cache.json`
- `.logs/spelling_inspection_overlay.log`
- `.logs/drag_apply.log`
- `.logs/replacement_structure.log`
- `.logs/ui_input_events.log`

이식 후 디버깅 우선순위:

1. 브라우저에서 텍스트가 앱에 안 들어옴
   - 확장 popup 확인
   - `.logs/browser_extension_bridge.log`
2. 앱에는 들어오지만 원본 반영 실패
   - `browser_session_id` 존재 확인
   - `/command` polling 여부 확인
   - `.logs/browser_extension_bridge.log`의 `queue_apply`, `pop_command`, `applied`
3. 맞춤법 교정 실패
   - `server/.env`의 `OPENAI_API_KEY`
   - `.logs/ai_events.jsonl`
   - `/correct-public` 응답
4. 줄바꿈 문제
   - popup의 extraction strategy 확인
   - Gmail은 `gmail-lines`여야 함
   - 캡처 로그의 `newlines`, `blank_lines`, 후보 preview 확인

## 이식 순서 제안

1. 브라우저 확장 폴더를 먼저 이식한다.
2. `client/input/browser_extension_bridge.py`를 이식한다.
3. `realtime_text_monitor.py`에 브리지 우선 polling을 붙인다.
4. `OutputTarget` 또는 기존 원본 반영 구조에 `mode="browser_extension"`을 추가한다.
5. `output_applier.py`에 브라우저 확장 queue apply 경로를 추가한다.
6. UI에서 최신 브라우저 확장 target을 보존한다.
7. 확장 popup으로 네이버 카페/Gmail 텍스트 인식부터 검증한다.
8. 그 다음 원본 반영을 검증한다.
9. 마지막으로 OpenAI 맞춤법 교정을 붙인다.

## 수동 테스트 문장

줄바꿈과 빈 줄 확인용:

```text
강아지
123
456

멍멍이

고양이

123

ABC
```

맞춤법/문장 교정 확인용:

```text
안령하새요 저는 이유서입니다.
고양이를 귀엽고 강아지는 귀엽습니다.
엔터가 무시되지않는지 제대로 확인하기 위해서 한칸 띄울게요
마춤뻡은 일부러 틀리고 있습니다

이 문장은 줄바꿈이 한번일때 문장 분리가 잘 되는지 확인하는 문장입니다
이 문장은 중간에 .이 있으면 어떻게 처리되는지 확인하는 문장입니다.
```

Gmail에서 확인할 것:

- 입력 직후와 2~3초 후 앱의 줄바꿈이 유지되는지.
- 빈 줄이 화면보다 과하게 늘어나지 않는지.
- 확장 popup의 strategy가 `gmail-lines`인지.

네이버 카페에서 확인할 것:

- 빈 줄 포함 텍스트가 그대로 앱에 들어오는지.
- 원본 반영 시 텍스트 박스가 잠기지 않는지.

## 현재 알려진 리스크

- 일부 UI 문자열 인코딩이 깨져 있다. 기능 이식용으로 로직만 참고하고 문자열은 새로 작성하는 것이 좋다.
- HWP/Word 서식 보존 원본 반영은 아직 안정성이 낮다. 브라우저 확장 기능과 별개로 취급하라.
- Gmail 원본 반영은 텍스트 인식보다 더 까다로울 수 있다. 현재 테스트는 주로 인식 안정성 기준이다.
- 브라우저 확장 기능은 Chrome/Edge 확장 수동 로드가 필요하다.
- 확장 코드를 바꾸면 `chrome://extensions`에서 확장 새로고침, 대상 탭 새로고침이 필요하다.

## Codex에게 주는 핵심 지시

- 이 문서가 들어 있는 프로젝트 루트만 기준으로 삼아라. 특정 컴퓨터의 상위 폴더명이나 다른 프로젝트 폴더명을 기준으로 삼지 마라.
- 먼저 브라우저 확장과 브리지부터 이식하라.
- Gmail 줄바꿈 문제를 점수 조정 방식으로 해결하려 하지 말고, `gmail-lines` 결정적 serializer를 유지하라.
- 로컬 사이트 권한 팝업을 피하려면 사이트 context가 아니라 extension background worker가 `127.0.0.1:8766`에 fetch해야 한다.
- `.env`, DB, 로그, pycache는 이식 대상이 아니다.
- 테스트는 네이버 카페와 Gmail부터 시작하라.
