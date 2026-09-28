.PHONY: install test test-fast lint site-commands demo demo-web reliability-web serve ablate clean
# `python -m pytest`, not `pytest`: the tests spawn `python bench.py`, which must resolve to the
# interpreter Hotpath is installed into (see CONTRIBUTING.md).
install:
	pip install -e ".[dev]"
test:
	python -m pytest -q
test-fast:
	python -m pytest -q -m "not slow"
lint:
	ruff check .
site-commands:
	python scripts/gen_site_commands.py
demo:
	hotpath run configs/demo_repo.yaml
demo-web:
	python -m hotpath.cli run configs/slow_web_analytics.yaml
reliability-web:
	python scripts/reliability_slow_web_analytics.py
serve:
	hotpath serve configs/demo_repo.yaml
ablate:
	hotpath ablate configs/demo_repo.yaml
clean:
	rm -rf demo_repo/.hotpath demo_repo/.git targets/torch_transformer/.hotpath targets/torch_transformer/.git .pytest_cache
