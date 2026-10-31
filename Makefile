# Pure-Python tests (no NetBox) run with pytest; NetBox-backed tests run inside dev NetBox.
VENV ?= .venv
# Django-runner tests; the rest are pytest-only and run with `make test`.
NETBOX_TESTS = netbox_lantronix.tests.test_apply netbox_lantronix.tests.test_jobs netbox_lantronix.tests.test_views
PY = $(VENV)/bin/python

.PHONY: venv test test-netbox lint scan audit sbom
venv:
	uv venv --python 3.12 $(VENV) && uv pip install --python $(PY) -e ".[dev]"

test:
	$(PY) -m pytest -q

test-netbox:
	docker compose -f dev/netbox-docker/docker-compose.yml -f dev/netbox-docker/docker-compose.override.yml exec netbox /opt/netbox/netbox/manage.py test --keepdb --noinput $(NETBOX_TESTS)

lint:
	$(VENV)/bin/ruff check netbox_lantronix

scan:
	gitleaks detect --no-banner --redact

# Runtime dependencies only: a throwaway venv with just the plugin installed.
RUNTIME_VENV = build/runtime-venv

$(RUNTIME_VENV): pyproject.toml
	rm -rf $(RUNTIME_VENV) && uv venv -q --python 3.12 $(RUNTIME_VENV)
	uv pip install -q --python $(RUNTIME_VENV)/bin/python .

audit: $(RUNTIME_VENV)
	uv pip freeze --python $(RUNTIME_VENV)/bin/python | grep -v '^netbox-lantronix' > build/runtime-requirements.txt
	$(VENV)/bin/pip-audit --strict --no-deps --disable-pip -r build/runtime-requirements.txt

sbom: $(RUNTIME_VENV)
	$(VENV)/bin/cyclonedx-py environment --pyproject pyproject.toml --of JSON -o build/sbom.cdx.json $(RUNTIME_VENV)
