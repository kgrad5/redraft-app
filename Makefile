.PHONY: up dev test fmt

VENV  := .venv
PY    := $(VENV)/bin/python
STAMP := $(VENV)/.stamp

# Cold start is `make up && make dev` — these two prerequisites bootstrap
# themselves so no separate setup step is needed.
$(STAMP): pyproject.toml
	@python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 14))' \
	  || { echo "python3 is $$(python3 -V 2>&1), but this project needs >= 3.14"; exit 1; }
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e '.[dev]'
	@touch $@

# No prerequisite on purpose: this must fire only when .env is absent. Listing
# .env.example here would re-copy whenever git touches its mtime, silently
# destroying local credentials.
.env:
	cp .env.example .env

up: .env
	docker compose up -d --wait

dev: $(STAMP) .env
	set -a; . ./.env; set +a; $(PY) -m uvicorn redraft.main:app --reload \
	  --host "$${APP_HOST:-127.0.0.1}" --port "$${APP_PORT:-8000}"

test: $(STAMP)
	$(PY) -m pytest

# --fix before format: check --fix rewrites code without reformatting it, so
# running it last can leave the tree unformatted.
fmt: $(STAMP)
	$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
