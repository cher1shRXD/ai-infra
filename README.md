# Context Hub

Codex와 Claude Code가 작업 기록을 한 API 서버에 저장하고, 다음 에이전트가 관련 맥락을 검색하는 사내 도구다.

구성은 세 가지뿐이다.

```text
Codex / Claude Code ──HTTP──> Context API ──> PostgreSQL + pgvector
                                      └────> 로컬 Ollama 임베딩
```

## 서버 컴퓨터

Docker와 Ollama가 필요하다.

```bash
brew install ollama
brew services start ollama
ollama pull qwen3-embedding:0.6b

make up
```

- API: `http://<이 컴퓨터 IP>:8000`
- API 문서: `http://<이 컴퓨터 IP>:8000/docs`
- 에이전트 접속 주소: `http://interns-Mac-mini-2.local:8000`

## 다른 컴퓨터

이 저장소를 받은 뒤 사용하는 에이전트의 Skill과 Hook만 설치한다. URL, API key, 이름 설정은 없다.

```bash
# Codex
codex plugin marketplace add https://github.com/cher1shRXD/ai-infra
codex plugin add context-hub-skill@ai-infra-local
codex plugin add context-hub-hooks@ai-infra-local

# Claude Code
claude plugin marketplace add https://github.com/cher1shRXD/ai-infra --scope user
claude plugin install context-hub-skill@ai-infra-local --scope user
claude plugin install context-hub-hooks@ai-infra-local --scope user
```

첫 실행 때 플러그인이 서버를 찾아 자동 등록하고 `~/.context-hub/config.json`에 발급된 키를 저장한다. `performed_by`는 AI가 대화에서 확인한 실제 작업자 이름을 사용한다. 모르면 AI가 먼저 사용자에게 물어본다. 컴퓨터 계정명은 작업자 이름으로 사용하지 않는다.

두 컴퓨터는 같은 사내 네트워크나 회사 VPN에 있어야 한다. 이 API를 인터넷에 직접 공개하지 않는다.

## 다른 컴퓨터의 플러그인 업데이트

GitHub marketplace를 새로 고친 뒤 플러그인을 다시 설치한다.

```bash
# Codex
codex plugin marketplace upgrade ai-infra-local
codex plugin remove context-hub-skill@ai-infra-local
codex plugin remove context-hub-hooks@ai-infra-local
codex plugin add context-hub-skill@ai-infra-local
codex plugin add context-hub-hooks@ai-infra-local

# Claude Code
claude plugin marketplace update ai-infra-local
claude plugin update context-hub-skill@ai-infra-local --scope user
claude plugin update context-hub-hooks@ai-infra-local --scope user
```

업데이트 후 Codex와 Claude Code를 재시작한다.

## 동작

- Hook은 Codex·Claude Code 세션을 완료 시점에 API로 보낸다.
- 전송 실패분은 로컬 outbox에 보관했다가 다음 호출 때 재시도한다.
- 흔한 API key·token·password 패턴은 전송 전에 제거한다.
- 서버는 로컬 Ollama로 세션을 업무 공간에 분류한다.
- Skill은 시작할 때 관련 기억을 검색하고, 재사용 가치가 있는 결정·사실·인수인계를 기록한다.
- 일반 ChatGPT·Claude 웹 채팅은 수집하지 않는다.

## 운영 명령

```bash
make status
make test
make backup
make down
```

자세한 운영 메모는 [OPERATIONS.md](./OPERATIONS.md)에 있다.
