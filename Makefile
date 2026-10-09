VENV = .venv
PY = $(VENV)/bin/python

.PHONY: test lint install venv

venv: $(VENV)/bin/activate

$(VENV)/bin/activate:
	python3 -m venv $(VENV)

install: $(VENV)/bin/activate
	$(PY) -m pip install -q -e ".[dev]"

test: install
	$(PY) -m pytest tests/ -q

lint:
	$(PY) -m py_compile $$(find hlidskjalf -name "*.py")
