# Context Hub Skill

여러 AI 에이전트가 Context Hub의 프로젝트 기억을 같은 규칙으로 읽고 쓰기 위한 portable Agent Skill 프로젝트다.

## 제공 항목

- `skills/context-memory/SKILL.md`: 검색, 기록, 대체, 인수인계 판단 규칙
- `scripts/context.py`: Python 표준 라이브러리만 사용하는 HTTP CLI
- `plugin.json`: Codex와 ChatGPT에서 설치할 수 있는 portable plugin manifest
- `.claude-plugin/plugin.json`: Claude Code용 plugin manifest

설치 후 첫 호출에서 서버에 자동 등록된다. 별도 URL이나 API key 설정은 없다. 수동 memory 명령은 기본 `general` 프로젝트를 사용한다.
