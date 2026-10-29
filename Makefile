# Every command agents and humans need. See AGENTS.md.
# PYTHONPATH=src works around macOS marking uv's editable-install .pth file as hidden,
# which makes Python skip it.
UV ?= uv
RUN = PYTHONPATH=src $(UV) run
COMPOSE = docker compose -f deploy/docker-compose.yml
ARGS ?=

.PHONY: setup lint format typecheck test check backfill refresh build app fixtures \
        docker-build docker-up docker-down hypotheses requirements

setup:
	$(UV) sync --frozen

lint:
	$(RUN) ruff check src tests scripts
	$(RUN) ruff format --check src tests scripts

format:
	$(RUN) ruff format src tests scripts
	$(RUN) ruff check --fix src tests scripts

typecheck:
	$(RUN) mypy src

test:
	$(RUN) pytest

check: lint typecheck test

backfill:
	$(RUN) fightdb -v backfill $(ARGS)

refresh:
	$(RUN) fightdb -v refresh $(ARGS)

build:
	$(RUN) fightdb -v build $(ARGS)

hypotheses:
	$(RUN) fightdb hypothesis run-all

app:
	$(RUN) streamlit run src/fightdb/app/Home.py

fixtures:
	$(RUN) python scripts/make_fixtures.py $(ARGS)

requirements:
	$(UV) export --no-dev --no-hashes --no-emit-project -o requirements.txt

docker-build:
	$(COMPOSE) build

docker-up:
	$(COMPOSE) up -d --build

docker-down:
	$(COMPOSE) down
