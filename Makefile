.PHONY: setup up down status rotate-key install-backup-schedule test test-unit test-e2e backup backup-verify

setup:
	python3 scripts/setup_local.py

rotate-key:
	python3 scripts/setup_local.py --rotate
	docker compose up -d --force-recreate --wait

install-backup-schedule:
	python3 scripts/install_backup_schedule.py

up: setup
	docker compose up -d --build --wait --remove-orphans

down:
	docker compose down

status:
	docker compose ps
	curl -fsS http://127.0.0.1:8000/readyz

backup:
	@./scripts/backup.sh

backup-verify:
	@test -n "$(FILE)" || (echo "usage: make backup-verify FILE=backups/context-....dump" >&2; exit 2)
	@./scripts/verify_backup.sh "$(FILE)"

test: test-unit test-e2e

test-unit:
	docker run --rm -v "$(CURDIR)/apps/context-api:/app" -w /app ai-infra-api \
		python -m unittest discover -s tests -v

test-e2e:
	docker compose run --rm --no-deps \
		-e CONTEXT_HUB_URL=http://api:8000 \
		-v "$(CURDIR):/repo:ro" -w /repo api python tests/test_e2e.py
