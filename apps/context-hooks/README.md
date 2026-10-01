# Context Hub Hooks

회사에서 사용하는 Codex와 Claude Code의 완료된 세션을 Context Hub에 보관하는 Hook 프로젝트다.

## 동작

- `Stop`에서 매 응답 완료 시 checkpoint하고 `SessionEnd`에서 마지막으로 한 번 더 보장한다.
- Hook event의 `session_id`와 `transcript_path`를 사용한다.
- transcript에서 일반적인 API key, bearer token, password 패턴을 제거한다.
- 전송 전에 로컬 outbox에 기록하여 API 장애 시 다음 세션 종료 때 재시도한다.
- 같은 세션은 upsert하므로 checkpoint마다 중복 세션이 생기지 않는다.
- API는 원문을 저장한 뒤 로컬 Ollama 임베딩으로 접근 가능한 업무 공간과 유사도를 계산한다.
- 코드 저장소나 현재 디렉터리로 업무를 결정하지 않는다.

원문 transcript와 재사용 가능한 `memory`는 서로 다른 테이블이다. 원문 전체가 벡터 memory node가 되지는 않는다.

설치 후 첫 Hook 실행에서 서버에 자동 등록된다. 별도 URL이나 API key 설정은 없다.

## 지원 범위

- Codex local/desktop runtime의 trusted plugin Hook
- Claude Code CLI/IDE/desktop의 enabled plugin Hook

브라우저의 ChatGPT·Claude 웹 채팅과 다른 AI 제품은 이 Hook이 볼 수 없다. 해당 제품까지 포함하려면 제품별 enterprise audit/export API 연동이 추가로 필요하다.

## 런타임별 파일

- Codex: `plugin.json` → `hooks/codex-hooks.json`
- Claude Code: `.claude-plugin/plugin.json` + `hooks/hooks.json`
- 공통 collector: `hooks/session_end.py`

자동 발급된 설정은 `~/.context-hub/config.json`에 저장된다.
