.PHONY: install test demo serve ablate clean
install:
	pip install -e ".[dev]"
test:
	pytest -q
demo:
	hotpath run configs/demo_repo.yaml
serve:
	hotpath serve configs/demo_repo.yaml
ablate:
	hotpath ablate configs/demo_repo.yaml
clean:
	rm -rf demo_repo/.hotpath demo_repo/.git targets/torch_transformer/.hotpath targets/torch_transformer/.git .pytest_cache
