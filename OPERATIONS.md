# 운영 메모

## 시작

```bash
brew services start ollama
make up
make status
```

서버 컴퓨터는 Docker가 실행 중이고 절전 상태가 아니어야 한다.

## 다른 컴퓨터 연결

Skill과 Hook 플러그인을 설치하고 Codex 또는 Claude Code를 새로 시작한다. 첫 Hook/Skill 호출 때 `http://interns-Mac-mini-2.local:8000`으로 자동 등록된다. 별도 URL, key, 사용자 설정은 없다.

## 확인

```bash
curl http://<server-ip>:8000/readyz
docker compose logs --tail=100 api
ollama list
```

정상 응답은 `{"status":"ready"}`다. 연결이 안 되면 같은 LAN/VPN인지와 서버 방화벽의 TCP 8000 허용 여부를 확인한다.

## 백업

```bash
make install-backup-schedule
make backup
make backup-verify FILE=backups/context-YYYYMMDDTHHMMSSZ.dump
```
