.PHONY: run check format test poll

run:            ## poll continuously and send alerts
	uv run eq-notifier run

check:          ## lint, formatting and type checks
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check

format:         ## fix lint issues and reformat
	uv run ruff check --fix .
	uv run ruff format .

test:           ## run the test suite
	uv run pytest

poll:           ## one poll: source health and recent events, sends nothing
	uv run eq-notifier check
