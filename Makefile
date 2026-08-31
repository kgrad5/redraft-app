.PHONY: up dev test fmt pyversion

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

# The guard above runs only when the stamp is rebuilt, and it checks python3
# rather than the venv. This runs every time, against the interpreter that will
# actually execute, so a .venv left behind by a since-switched python3 cannot
# quietly serve a green run.
pyversion: $(STAMP)
	@$(PY) -c 'import sys; raise SystemExit(sys.version_info < (3, 14))' \
	  || { echo "$(PY) is $$($(PY) -V 2>&1), but this project needs >= 3.14;"\
	       "delete $(VENV) and re-run"; exit 1; }

# No prerequisite on purpose: this must fire only when .env is absent. Listing
# .env.example here would re-copy whenever git touches its mtime, silently
# destroying local credentials.
.env:
	cp .env.example .env

up: .env
	docker compose up -d --wait

dev: pyversion .env
	set -a; . ./.env; set +a; $(PY) -m uvicorn redraft.main:app --reload \
	  --host "$${APP_HOST:-127.0.0.1}" --port "$${APP_PORT:-8000}"

# `up` rather than a bare .env: most of the suite needs Postgres, and without it
# 19 tests error on connection — a red that reads as a regression but is only a
# stopped container. The prerequisite states the precondition rather than leaving
# it to be diagnosed, and `up` supplies .env on the way.
test: pyversion up
	$(PY) -m pytest

# --fix before format: check --fix rewrites code without reformatting it, so
# running it last can leave the tree unformatted. The leading - stops an unfixable
# violation from aborting the target before the formatter runs; the trailing check
# then reports what --fix could not repair, rather than exiting 0 over it.
fmt: pyversion
	-$(PY) -m ruff check --fix .
	$(PY) -m ruff format .
	$(PY) -m ruff check .
