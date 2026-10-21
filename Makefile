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
		-r $(ROOT)/n8n-hubspot-sheets-slack/requirements.txt \
		-r $(ROOT)/pdf-document-extraction/requirements-dev.txt \
		-r $(ROOT)/ai-invoice-field-extractor/requirements-dev.txt \
		-r $(ROOT)/pipeline-reliability-audit/requirements-dev.txt \
		-r $(ROOT)/data-quality-monitoring/requirements-dev.txt \
		-r $(ROOT)/data-cleaning-toolkit/requirements-dev.txt

test: deps
	cd $(ROOT)/gov-api-pipeline && $(PY) -m pytest -q
	cd $(ROOT)/toscrape-scraper && $(PY) -m pytest -q
	cd $(ROOT)/n8n-hubspot-sheets-slack && $(PY) -m pytest -q
	cd $(ROOT)/pdf-document-extraction && $(PY) -m pytest -q
	cd $(ROOT)/ai-invoice-field-extractor && $(PY) -m pytest -q
	cd $(ROOT)/pipeline-reliability-audit && $(PY) -m pytest -q
	cd $(ROOT)/data-quality-monitoring && $(PY) -m pytest -q
	cd $(ROOT)/data-cleaning-toolkit && $(PY) -m pytest -q
