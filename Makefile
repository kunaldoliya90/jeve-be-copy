# JEVE-BE development commands. Run from the repository root.

.PHONY: init start stop runserver runtests

init: ## Create .venv and install app/requirements/development.txt
	uv venv --allow-existing
	uv pip install -r app/requirements/development.txt

start: ## Build and start the local stack in Docker (FLAVOUR=dev)
	docker compose up --build --detach

stop: ## Stop the local stack
	docker compose down

runserver: ## Run the API on the host with auto-reload (FLAVOUR=dev)
	uv run python app/main.py runserver

runtests: ## Run all tests
	uv run pytest
