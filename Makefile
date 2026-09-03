.PHONY: up dev snapshot test fmt pyversion

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

# One run of every ingester: a dated snapshot per JSON source, and `players` upserted
# in place. `up` for the same reason `test` takes it — this target's whole purpose is
# to write rows, so without Postgres every source fails on connection, a red that reads
# as four dead feeds when it is one stopped container.
#
# The schedule is not in this repo, on purpose: nothing in the process schedules
# anything and no table records that a run happened. Install it once with `crontab -e`.
# cron's PATH is /usr/bin:/bin, which has no docker, and cron mails a job's output only
# where an MTA is configured — which is not the default here — so the log file below is
# the review surface for the unmatched-player report, not mail:
#
#   PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin
#   30 4 * * * cd /path/to/redraft-app && make snapshot >> /path/to/redraft-app/snapshot.log 2>&1
#
# The job exits 1 when any source failed; make reports a failed recipe as its own exit 2.
snapshot: pyversion up
	set -a; . ./.env; set +a; $(PY) -m redraft.jobs.daily

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
