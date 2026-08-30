.PHONY: up dev test fmt

VENV  := .venv
PY    := $(VENV)/bin/python
STAMP := $(VENV)/.stamp

# Cold start is `make up && make dev` — these two prerequisites bootstrap
# themselves so no separate setup step is needed.
$(STAMP): pyproject.toml
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -e '.[dev]'
	@touch $@

.env: .env.example
	cp .env.example .env

up: .env
	docker compose up -d --wait

dev: $(STAMP) .env
	set -a; . ./.env; set +a; $(PY) -m uvicorn redraft.main:app --reload --host $$APP_HOST --port $$APP_PORT

# pytest exits 5 when it collects zero tests, which this issue requires to pass.
# Every other exit code propagates unchanged.
test: $(STAMP)
	@$(PY) -m pytest; code=$$?; [ $$code -eq 5 ] && exit 0 || exit $$code

fmt: $(STAMP)
	$(PY) -m ruff format .
	$(PY) -m ruff check --fix .
