.PHONY: install seed serve test eval demo clean docker-build docker-run

VENV ?= .venv
PY   ?= $(VENV)/bin/python

install:                       ## create the virtualenv and install dependencies
	python3 -m venv $(VENV)
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r requirements.txt

seed:                          ## ingest, connect demo systems, analyse, run agents
	@$(PY) -m veris.cli seed

serve:                         ## run the API and workspace
	$(PY) -m veris.cli serve --host 0.0.0.0 --port 8000

test:                          ## contract and regression tests
	@$(PY) tests/test_connectors.py
	@$(PY) tests/test_migrations.py
	@$(PY) tests/test_relevance_floor.py

eval:                          ## measure the intelligence against ground truth
	@$(PY) -m eval.run_eval

demo: seed eval serve          ## seed, verify, then serve

clean:                         ## remove generated data, keep source documents
	rm -f data/veris.db data/veris.db-wal data/veris.db-shm
	rm -rf data/canonical data/originals

docker-build:
	docker build -t veris:0.1 .

docker-run:
	docker run --rm -p 8000:8000 --env-file .env -v veris-data:/app/data veris:0.1
