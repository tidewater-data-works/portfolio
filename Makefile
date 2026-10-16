# Offline tests for every Tidewater Data Works demo.
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PYTHON3 ?= python3
VENV := $(ROOT)/.venv
PY := $(VENV)/bin/python

.PHONY: test deps

deps:
	$(PYTHON3) -m venv $(VENV)
	$(PY) -m pip install -q -U pip
	$(PY) -m pip install -q \
		-r $(ROOT)/gov-api-pipeline/requirements-dev.txt \
		-r $(ROOT)/toscrape-scraper/requirements-dev.txt \
		-r $(ROOT)/n8n-hubspot-sheets-slack/requirements.txt

test: deps
	cd $(ROOT)/gov-api-pipeline && $(PY) -m pytest -q
	cd $(ROOT)/toscrape-scraper && $(PY) -m pytest -q
	cd $(ROOT)/n8n-hubspot-sheets-slack && $(PY) -m pytest -q
