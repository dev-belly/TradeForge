# TradeForge development entry points.
#
# Every target here runs on a machine with nothing but a Python toolchain. The
# C++ targets are the exception, and they say so when a compiler is absent.
#
#   make demo        one command, no downloads, no API keys
#   make test        the Python suite
#   make research    the pre-registered experiment grids
#
# Nothing in this file downloads market data. The bundled generator is
# deterministic and its output is labelled SYNTHETIC in every artefact.

.DEFAULT_GOAL := help

PY        ?= python
PYTEST    ?= $(PY) -m pytest
CMAKE     ?= cmake
BUILD     ?= build
BUILD_SAN ?= build-san
CONFIGS   ?= configs
ARTIFACTS ?= artifacts

.PHONY: help install install-full doctor build-cpp test test-python test-all test-cpp \
        test-differential sanitize lint format typecheck demo run-all research ml \
        benchmark report api dashboard db-init db-list db-query clean distclean

help: ## Show the available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------- setup

install: ## Install the package with development tooling
	$(PY) -m pip install -e ".[dev]"

install-full: ## Install everything, including the API and dashboard extras
	$(PY) -m pip install -e ".[dev,full]"

doctor: ## Report the engine backend and which optional extras are present
	$(PY) -m tradeforge.interfaces.cli doctor --configs $(CONFIGS)

# ------------------------------------------------------------------ C++ core

build-cpp: ## Build the C++ core and the pybind11 extension
	$(CMAKE) -S . -B $(BUILD) -DCMAKE_BUILD_TYPE=Release -DTRADEFORGE_BUILD_PYTHON=ON
	$(CMAKE) --build $(BUILD) --parallel
	@echo "extension: $(BUILD)/python"
	@PYTHONPATH=$(BUILD)/python:src $(PY) -c \
		"from tradeforge.infrastructure.cpp_bridge import core_status; \
		 s = core_status(); print('backend:', s['backend']); print(s['detail'])"

test-cpp: ## Build and run the C++ unit tests (downloads GoogleTest if needed)
	$(CMAKE) -S . -B $(BUILD) -DCMAKE_BUILD_TYPE=Release -DTRADEFORGE_BUILD_TESTS=ON \
		-DTRADEFORGE_FETCH_GTEST=ON
	$(CMAKE) --build $(BUILD) --parallel
	cd $(BUILD) && ctest --output-on-failure

test-differential: build-cpp ## Compare the Python reference against the C++ core
	PYTHONPATH=$(BUILD)/python:src $(PYTEST) tests/differential -q

sanitize: ## Build the C++ tests with ASan + UBSan and run them
	$(CMAKE) -S . -B $(BUILD_SAN) -DCMAKE_BUILD_TYPE=Debug \
		-DTRADEFORGE_BUILD_TESTS=ON -DTRADEFORGE_FETCH_GTEST=ON -DTRADEFORGE_SANITIZE=ON
	$(CMAKE) --build $(BUILD_SAN) --parallel
	cd $(BUILD_SAN) && ctest --output-on-failure

# --------------------------------------------------------------------- tests

test: ## Run the Python test suite
	$(PYTEST) tests -q

test-python: test ## Alias for `test`

test-all: test test-differential ## Python suite plus the Python/C++ parity suite

# ------------------------------------------------------------------- quality

lint: ## Lint and check formatting
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests

format: ## Apply formatting
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests

typecheck: ## Static type check
	$(PY) -m mypy src/tradeforge

# ------------------------------------------------------------------ research

demo: ## Run the four algorithms on synthetic data and compare them
	$(PY) -m tradeforge.interfaces.cli demo --configs $(CONFIGS)

run-all: ## Run a grid of executions and store the artefacts as Parquet
	$(PY) -m benchmarks.run_runs --output $(ARTIFACTS)/runs --configs $(CONFIGS)

research: ## Run the pre-registered experiment grids and record every cell
	$(PY) -m tradeforge.interfaces.cli research run --experiment all --seeds 3 \
		--output $(ARTIFACTS)/research --configs $(CONFIGS)

ml: ## Fit the fill-probability baselines on a purged time split
	$(PY) -m tradeforge.interfaces.cli ml fill-probability --configs $(CONFIGS)

benchmark: ## Measure throughput and write artifacts/benchmarks/*.json
	$(PY) -m benchmarks.run_benchmarks --output $(ARTIFACTS)/benchmarks

report: ## Render the self-contained HTML reports
	$(PY) -m tradeforge.interfaces.cli report --output $(ARTIFACTS)/reports \
		--configs $(CONFIGS)

# ---------------------------------------------------------------- interfaces

api: ## Start the HTTP API on 127.0.0.1:8000
	$(PY) -m uvicorn tradeforge.interfaces.api:app --host 127.0.0.1 --port 8000

dashboard: ## Start the Streamlit dashboard (reads artefacts)
	$(PY) -m streamlit run src/tradeforge/interfaces/dashboard.py

# -------------------------------------------------------------------- storage

db-init: ## Create the Parquet layout and verify the schema compiles
	$(PY) -m tradeforge.interfaces.cli db init --root $(ARTIFACTS)/runs

db-list: ## List the packaged analytical queries
	$(PY) -m tradeforge.interfaces.cli db list --sql-dir sql

db-query: ## Run one packaged query: make db-query NAME=01_execution_summary
	$(PY) -m tradeforge.interfaces.cli db query $(NAME) --root $(ARTIFACTS)/runs --sql-dir sql

# --------------------------------------------------------------------- clean

clean: ## Remove caches and test artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis
	find . -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

distclean: clean ## Also remove the build directory and generated artefacts
	rm -rf $(BUILD) $(BUILD_SAN) $(ARTIFACTS) dist *.egg-info
