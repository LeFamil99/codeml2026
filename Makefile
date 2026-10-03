# L2C Review - common commands.  Run `make` or `make help` to list targets.
# Override any variable on the command line, e.g.  make run PROJECT=WP2

VENV     := .venv
PY       := $(VENV)/bin/python
PIP      := $(VENV)/bin/pip
L2C      := $(VENV)/bin/l2c
STREAMLIT:= $(VENV)/bin/streamlit

CORPUS   ?= $(HOME)/Downloads/l2c-participants
PROJECT  ?= CLP
OUT      ?= out
PORT     ?= 8501
PROJECTS := CLP WP2 LIGREP EspCa3B

export L2C_CORPUS = $(CORPUS)

.DEFAULT_GOAL := help
.PHONY: help install ui stop run run-all validate validate-all test test-coords \
        test-e2e test-ui truth summary clean purge doctor freeze

help: ## Show this help
	@echo "L2C Review - make targets"
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "Variables:  PROJECT=$(PROJECT)  OUT=$(OUT)  PORT=$(PORT)"
	@echo "            CORPUS=$(CORPUS)"

install: ## Create the venv and install everything (editable)
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -e ".[app,dev]"
	@echo "ready - try 'make ui'"

# ---------------------------------------------------------------- running
ui: ## Launch the dashboard (PORT=8501)
	$(STREAMLIT) run app/streamlit_app.py --server.port $(PORT)

stop: ## Stop a dashboard left running on PORT
	@pkill -f "streamlit run app/streamlit_app.py" && echo "stopped" || echo "nothing running"

run: ## Extract one project -> JSON  (PROJECT=CLP)
	$(L2C) run $(CORPUS)/$(PROJECT) --out $(OUT)

run-all: ## Extract every project
	@for p in $(PROJECTS); do \
	  echo "=== $$p"; $(L2C) run $(CORPUS)/$$p --out $(OUT) --quiet | grep -E '^(unit|sheets|elements|elapsed)'; \
	done

validate: ## Check one project's JSON against Appendix A
	$(L2C) validate $(OUT)/$(PROJECT)/elements_plan.json

validate-all: ## Check every project's JSON (skips any not yet extracted)
	@miss=0; for p in $(PROJECTS); do printf "%-9s " $$p; \
	  if [ -f "$(OUT)/$$p/elements_plan.json" ]; then $(L2C) validate $(OUT)/$$p/elements_plan.json; \
	  else echo "not extracted - run 'make run-all'"; miss=1; fi; done; exit 0

# ---------------------------------------------------------------- checking
test: ## Run the whole suite against the real corpus
	$(PY) -m pytest tests -q

test-coords: ## Only the coordinate-trap guards (MediaBox origin, /Rotate 90)
	$(PY) -m pytest tests/test_coords.py -v

test-e2e: ## Only the end-to-end corpus runs
	$(PY) -m pytest tests/test_end_to_end.py -v

test-ui: ## Only the headless dashboard tests
	$(PY) -m pytest tests/test_app.py -v

truth: ## Print the answer-key row the pipeline must derive (S-502 / K-6 / 4-35M)
	@$(PY) -c "from l2c.pipeline import run_plan; \
import os; r=run_plan(os.path.join('$(CORPUS)','CLP','L2C_PLAN_STR_CLP.pdf')); \
k=[x for x in r.records if x.feuillet=='S-502' and x.element=='K-6'][0]; \
a=k.armature[0]; \
print('S-502  K-6  ->  %d-%s   (answer key: 4-35M)  %s'%(a.quantite,a.diametre,'PASS' if (a.quantite,a.diametre)==(4,'35M') else 'FAIL'))"

summary: ## One-line extraction summary per project
	@printf "%-9s %7s %9s %14s %-9s\n" PROJECT SHEETS ELEMENTS LOCATED UNITS
	@$(PY) -c "import os; from l2c.pipeline import run_plan, find_plan; \
[print('%-9s %7d %9d %14s %-9s'%(p, r.totals['sheets_extracted'], r.totals['elements'], \
'%d (%.1f%%)'%(r.totals['located'], 100*r.totals['located']/max(1,r.totals['elements'])), r.unit_system)) \
 for p in '$(PROJECTS)'.split() for r in [run_plan(find_plan(os.path.join('$(CORPUS)',p)))]]"

freeze: ## Pin exact versions to requirements.txt (reproducibility)
	$(PIP) freeze --exclude-editable > requirements.txt
	@echo "wrote requirements.txt ($$(wc -l < requirements.txt) packages)"

doctor: ## Check the environment and corpus are usable
	@echo "python      $$($(PY) --version 2>&1)"
	@echo "pymupdf     $$($(PY) -c 'import pymupdf;print(pymupdf.__doc__.splitlines()[0])' 2>&1 | head -1)"
	@echo "corpus      $(CORPUS)"
	@for p in $(PROJECTS); do \
	  if [ -d "$(CORPUS)/$$p" ]; then echo "  [ok]   $$p"; else echo "  [MISSING] $$p"; fi; \
	done

# ---------------------------------------------------------------- housekeeping
clean: ## Remove generated output and caches (keeps the venv)
	rm -rf $(OUT) .pytest_cache .cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	@echo "cleaned"

purge: ## Delete generated output AND any corpus copy under this repo (consignes S4)
	@$(MAKE) clean
	rm -rf ./l2c-participants ./*participants*
	@echo "purged - remember the corpus must be deleted from the workstation after the event"
