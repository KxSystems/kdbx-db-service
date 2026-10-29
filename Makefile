PYTHON ?= python3
MAKEFLAGS += --no-print-directory
TEST_SUITE ?= notebooks
TEST_SOURCE ?=
TEST_FILTER ?=
FAIL_FAST ?= 0
DBSERVICE_RESET_DATA ?= 1
VERBOSE ?= 0
VALIDATION_LOG_DIR ?= validation-logs

ifneq ($(origin BATCH),undefined)
$(error BATCH has been removed. Use TEST_SUITE)
endif
ifneq ($(origin ONLY),undefined)
$(error ONLY has been removed. Use TEST_FILTER)
endif
ifneq ($(origin SPEC_FILE),undefined)
$(error SPEC_FILE has been removed. Use TEST_SOURCE)
endif
ifneq ($(origin README_FILE),undefined)
$(error README_FILE has been removed. Use TEST_SOURCE)
endif
ifneq ($(origin SRC_DBS),undefined)
$(error SRC_DBS has been removed. Use CODE_DBS)
endif
ifneq ($(origin SRC_QCLIENT),undefined)
$(error SRC_QCLIENT has been removed. Use CODE_QCLIENT)
endif
ifneq ($(origin SRC_PYCLIENT),undefined)
$(error SRC_PYCLIENT has been removed. Use CODE_PYCLIENT)
endif
ifneq ($(origin DBS_VERSION),undefined)
$(error DBS_VERSION has been removed. Set CODE_DBS to the exact release version)
endif

# CODE_PROFILE supplies component defaults. CODE_DBS, CODE_QCLIENT, and
# CODE_PYCLIENT can override one component for mixed-version validation.
CODE_PROFILE ?= main
ifeq ($(filter main release local,$(CODE_PROFILE)),)
$(error Unsupported CODE_PROFILE '$(CODE_PROFILE)'. Use main, release, or local)
endif
CODE_DBS ?= $(CODE_PROFILE)
CODE_QCLIENT ?= $(CODE_PROFILE)
CODE_PYCLIENT ?= $(CODE_PROFILE)
ifeq ($(CODE_DBS),latest)
$(error CODE_DBS=latest is ambiguous. Use CODE_DBS=main or CODE_DBS=release)
endif

DBSERVICE_MAIN_REGISTRY ?= dev-registry.kxi-dev.kx.com/
DBSERVICE_MAIN_TAG ?= latest
DBSERVICE_MAIN_RT_TAG ?= 1.18.0
DBSERVICE_RELEASE_ENV_FILE ?= $(CURDIR)/test/validation/release.env
DBSERVICE_RELEASE_REGISTRY ?= $(strip $(shell . "$(DBSERVICE_RELEASE_ENV_FILE)" 2>/dev/null && printf '%s' "$$DS_REGISTRY"))
DBSERVICE_RELEASE_TAG ?= $(strip $(shell . "$(DBSERVICE_RELEASE_ENV_FILE)" 2>/dev/null && printf '%s' "$$DS_TAG"))
DBSERVICE_RELEASE_RT_TAG ?= $(strip $(shell . "$(DBSERVICE_RELEASE_ENV_FILE)" 2>/dev/null && printf '%s' "$$DS_RT_TAG"))
DBSERVICE_LOCAL_REGISTRY ?=
DBSERVICE_LOCAL_TAG ?= debug
DBSERVICE_LOCAL_RT_TAG ?= 1.18.0-rc.26
DBSERVICE_Q_CLIENT_GITLAB_URL ?= https://gitlab.com/kxdev/kxinsights/data-services/dbservice-qclient.git
DBSERVICE_Q_CLIENT_GITHUB_URL ?= https://github.com/KxSystems/kdbx-db-service-q-client.git
DBSERVICE_Q_CLIENT_MAIN_REF ?= main
DBSERVICE_Q_CLIENT_RELEASE_REF ?= main
DBSERVICE_PYTHON_CLIENT_GITLAB_URL ?= https://gitlab.com/kxdev/kxinsights/data-services/dbservice-python-client.git
DBSERVICE_PYTHON_CLIENT_GITHUB_URL ?= https://github.com/KxSystems/kdbx-db-service-python-client.git
DBSERVICE_PYTHON_CLIENT_MAIN_REF ?= main
DBSERVICE_PYTHON_CLIENT_RELEASE_REF ?= main
DBSERVICE_Q_CLIENT_SIBLING_DIR ?= ../dbservice-qclient
DBSERVICE_PYTHON_CLIENT_SIBLING_DIR ?= ../dbservice-python-client

DBSERVICE_Q_CLIENT_DIR ?= /tmp/kdbx-db-service-q-client

ifneq ($(filter /% ./% ../%,$(CODE_QCLIENT)),)
DBSERVICE_Q_CLIENT_LOCAL_DIR ?= $(CODE_QCLIENT)
else ifeq ($(CODE_QCLIENT),local)
DBSERVICE_Q_CLIENT_LOCAL_DIR ?= $(DBSERVICE_Q_CLIENT_SIBLING_DIR)
else ifeq ($(CODE_QCLIENT),release)
DBSERVICE_Q_CLIENT_URL ?= $(DBSERVICE_Q_CLIENT_GITHUB_URL)
DBSERVICE_Q_CLIENT_REF ?= $(DBSERVICE_Q_CLIENT_RELEASE_REF)
else ifeq ($(CODE_QCLIENT),main)
DBSERVICE_Q_CLIENT_URL ?= $(DBSERVICE_Q_CLIENT_GITLAB_URL)
DBSERVICE_Q_CLIENT_REF ?= $(DBSERVICE_Q_CLIENT_MAIN_REF)
else
DBSERVICE_Q_CLIENT_URL ?= $(DBSERVICE_Q_CLIENT_GITLAB_URL)
DBSERVICE_Q_CLIENT_REF ?= $(CODE_QCLIENT)
endif

ifneq ($(filter /% ./% ../%,$(CODE_PYCLIENT)),)
DBSERVICE_PYTHON_CLIENT_DIR ?= $(CODE_PYCLIENT)
DBSERVICE_PYTHON_CLIENT_MODE ?= source
else ifeq ($(CODE_PYCLIENT),local)
DBSERVICE_PYTHON_CLIENT_DIR ?= $(DBSERVICE_PYTHON_CLIENT_SIBLING_DIR)
DBSERVICE_PYTHON_CLIENT_MODE ?= source
else ifeq ($(CODE_PYCLIENT),release)
DBSERVICE_PYTHON_CLIENT_MODE ?= package
DBSERVICE_PYTHON_CLIENT_URL ?= $(DBSERVICE_PYTHON_CLIENT_GITHUB_URL)
DBSERVICE_PYTHON_CLIENT_REF ?= $(DBSERVICE_PYTHON_CLIENT_RELEASE_REF)
else ifeq ($(CODE_PYCLIENT),main)
DBSERVICE_PYTHON_CLIENT_MODE ?= source
DBSERVICE_PYTHON_CLIENT_URL ?= $(DBSERVICE_PYTHON_CLIENT_GITLAB_URL)
DBSERVICE_PYTHON_CLIENT_REF ?= $(DBSERVICE_PYTHON_CLIENT_MAIN_REF)
else
DBSERVICE_PYTHON_CLIENT_MODE ?= source
DBSERVICE_PYTHON_CLIENT_URL ?= $(DBSERVICE_PYTHON_CLIENT_GITLAB_URL)
DBSERVICE_PYTHON_CLIENT_REF ?= $(CODE_PYCLIENT)
endif

ifeq ($(CODE_DBS),release)
DS_REGISTRY ?= $(DBSERVICE_RELEASE_REGISTRY)
DS_TAG ?= $(DBSERVICE_RELEASE_TAG)
DS_RT_TAG ?= $(DBSERVICE_RELEASE_RT_TAG)
ifeq ($(strip $(DS_REGISTRY)),)
$(error CODE_DBS=release could not read DS_REGISTRY from $(DBSERVICE_RELEASE_ENV_FILE))
endif
ifeq ($(strip $(DS_TAG)),)
$(error CODE_DBS=release could not read DS_TAG from $(DBSERVICE_RELEASE_ENV_FILE))
endif
ifeq ($(strip $(DS_RT_TAG)),)
$(error CODE_DBS=release could not read DS_RT_TAG from $(DBSERVICE_RELEASE_ENV_FILE))
endif
else ifeq ($(CODE_DBS),main)
DS_REGISTRY ?= $(DBSERVICE_MAIN_REGISTRY)
DS_TAG ?= $(DBSERVICE_MAIN_TAG)
DS_RT_TAG ?= $(DBSERVICE_MAIN_RT_TAG)
else ifeq ($(CODE_DBS),local)
DS_REGISTRY ?= $(DBSERVICE_LOCAL_REGISTRY)
DS_TAG ?= $(DBSERVICE_LOCAL_TAG)
DS_RT_TAG ?= $(DBSERVICE_LOCAL_RT_TAG)
else
DS_REGISTRY ?= $(DBSERVICE_RELEASE_REGISTRY)
DS_TAG ?= $(CODE_DBS)
DS_RT_TAG ?= $(DBSERVICE_RELEASE_RT_TAG)
ifeq ($(strip $(DS_REGISTRY)),)
$(error CODE_DBS=$(CODE_DBS) could not read the portal registry from $(DBSERVICE_RELEASE_ENV_FILE))
endif
ifeq ($(strip $(DS_RT_TAG)),)
$(error CODE_DBS=$(CODE_DBS) could not read the RT tag from $(DBSERVICE_RELEASE_ENV_FILE))
endif
endif
DBSERVICE_COMPOSE ?= docker compose -f docker-compose.yaml
NOTEBOOK_TEST_COMPOSE ?= docker compose -f docker-compose.yaml -f test/compose/docker-compose.notebook-test-runner.yaml
DBSERVICE_ENDPOINT ?= $(if $(NOTEBOOK_IMPORT_STATUS_ENDPOINT),$(NOTEBOOK_IMPORT_STATUS_ENDPOINT),http://localhost:8080)
DBSERVICE_SERVICES ?= kx-db-rt kx-db-sm kx-db-da kx-db-rc kx-db-agg kx-db-gw
DBSERVICE_CONTAINERS ?= notebook-test-runner-ci kx-db-rt kx-db-sm kx-db-da kx-db-rc kx-db-agg kx-db-gw
DBSERVICE_RT_LOCAL_SOURCE ?= $(DBSERVICE_MAIN_REGISTRY)kxi-rt:$(DS_RT_TAG)
DBSERVICE_RT_LOCAL_TARGET ?= kxi-rt:$(DS_RT_TAG)
VALIDATION_STEP = VERBOSE="$(VERBOSE)" sh test/validation/run_step.sh

.PHONY: dbservice-local-image-tags dbservice-pull-images dbservice-clean-containers dbservice-init-volumes dbservice-seed-imports dbservice-up dbservice-down validate

dbservice-local-image-tags:
	@if [ -z "$(DS_REGISTRY)" ]; then \
		if ! docker image inspect "$(DBSERVICE_RT_LOCAL_TARGET)" >/dev/null 2>&1; then \
			if docker image inspect "$(DBSERVICE_RT_LOCAL_SOURCE)" >/dev/null 2>&1; then \
				echo "Tagging local RT image $(DBSERVICE_RT_LOCAL_SOURCE) as $(DBSERVICE_RT_LOCAL_TARGET)"; \
				docker tag "$(DBSERVICE_RT_LOCAL_SOURCE)" "$(DBSERVICE_RT_LOCAL_TARGET)"; \
			else \
				echo "Missing local RT image $(DBSERVICE_RT_LOCAL_TARGET)."; \
				echo "Also checked $(DBSERVICE_RT_LOCAL_SOURCE)."; \
				echo "Set DS_RT_TAG or tag/pull the matching RT image before validation."; \
				exit 1; \
			fi; \
		fi; \
	fi

dbservice-pull-images:
	@if [ "$(CODE_DBS)" = "main" ]; then \
		$(VALIDATION_STEP) "Pulling current DB Service main images" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
			env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
			$(DBSERVICE_COMPOSE) pull $(DBSERVICE_SERVICES); \
	fi

dbservice-clean-containers:
	@if [ "$(VERBOSE)" = "1" ]; then printf '%s\n' '-> Removing stale DB Service validation containers'; fi
	@docker rm -f $(DBSERVICE_CONTAINERS) >/dev/null 2>&1 || true

dbservice-init-volumes:
	@$(VALIDATION_STEP) "Preparing SM writable directories" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
		env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
		$(DBSERVICE_COMPOSE) run --rm --no-deps --user 0:0 --entrypoint sh kx-db-sm \
		-c 'mkdir -p /db/idb /db/hdb /imports /logs && chmod -R 777 /db /imports /logs'
	@$(VALIDATION_STEP) "Preparing RT writable directories" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
		env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
		$(DBSERVICE_COMPOSE) run --rm --no-deps --user 0:0 --entrypoint sh kx-db-rt \
		-c 'mkdir -p /s/in /s/out /s/state /s/session && chmod -R 777 /s'

dbservice-seed-imports:
	@if [ -d data/imports ]; then \
		visible=1; \
		for fixture in fxquote.csv.gz fxquote.parquet instruments.csv forex.csv fxquote-hdb/fxquote; do \
			if [ -e "data/imports/$$fixture" ] && ! docker exec kx-db-sm test -e "/imports/$$fixture"; then \
				visible=0; \
			fi; \
		done; \
		if [ "$$visible" != "1" ]; then \
			$(VALIDATION_STEP) "Copying sample imports into DB Service" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
				docker cp data/imports/. kx-db-sm:/imports/ || exit $$?; \
		fi; \
		for fixture in fxquote.csv.gz fxquote.parquet instruments.csv forex.csv fxquote-hdb/fxquote; do \
			if [ -e "data/imports/$$fixture" ]; then \
				docker exec kx-db-sm test -e "/imports/$$fixture"; \
			fi; \
		done; \
		docker exec --user 0:0 kx-db-sm chmod -R 777 /imports; \
	fi

dbservice-up: dbservice-local-image-tags dbservice-pull-images
	@if [ "$(VERBOSE)" = "1" ]; then printf '\n== Setup: DB Service ==\n'; fi
	@mkdir -p data/db data/imports data/logs data/rt
	@if [ "$(DBSERVICE_RESET_DATA)" = "1" ]; then \
		$(VALIDATION_STEP) "Resetting SM writable data" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
			env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
			$(DBSERVICE_COMPOSE) run --rm --no-deps --user 0:0 --entrypoint sh kx-db-sm \
			-c 'rm -rf /db/* /db/.[!.]* /db/..?* /logs/* /logs/.[!.]* /logs/..?*' || exit $$?; \
		$(VALIDATION_STEP) "Resetting RT writable data" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
			env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
			$(DBSERVICE_COMPOSE) run --rm --no-deps --user 0:0 --entrypoint sh kx-db-rt \
			-c 'rm -rf /s/* /s/.[!.]* /s/..?*' || exit $$?; \
	fi
	@mkdir -p data/db data/db/idb data/db/hdb data/imports data/logs data/rt/in data/rt/out data/rt/state data/rt/session
	@if [ -d samples/data ]; then cp -R samples/data/. data/imports/; fi
	@if [ -f samples/data/fxquote ]; then mkdir -p data/imports/fxquote-hdb; cp samples/data/fxquote data/imports/fxquote-hdb/fxquote; fi
	@$(MAKE) dbservice-init-volumes
	@$(VALIDATION_STEP) "Starting DB Service containers" "$(VALIDATION_LOG_DIR)/dbservice-setup.log" \
		env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
		$(DBSERVICE_COMPOSE) up --force-recreate --remove-orphans -d $(DBSERVICE_SERVICES)
	@if [ "$(VERBOSE)" = "1" ]; then printf '%s\n' '-> Waiting for DB Service at $(DBSERVICE_ENDPOINT)'; fi
	@for i in $$(seq 1 60); do \
		if NOTEBOOK_IMPORT_STATUS_ENDPOINT="$(DBSERVICE_ENDPOINT)" $(PYTHON) test/check_dbservice.py >/dev/null 2>&1; then \
			if [ "$(VERBOSE)" = "1" ]; then echo "DB Service is reachable at $(DBSERVICE_ENDPOINT)"; fi; \
			exit 0; \
		fi; \
		sleep 2; \
	done; \
	printf '\nVALIDATION SETUP FAILED\nStage: Waiting for DB Service\nEndpoint: %s\n' "$(DBSERVICE_ENDPOINT)" >&2; \
	NOTEBOOK_IMPORT_STATUS_ENDPOINT="$(DBSERVICE_ENDPOINT)" $(PYTHON) test/check_dbservice.py
	@$(MAKE) dbservice-seed-imports

dbservice-down:
	@if [ "$(VERBOSE)" = "1" ]; then printf '\n== Teardown: DB Service ==\n'; fi
	@mkdir -p executed-notebook validation-reports
	@docker cp notebook-test-runner-ci:/workspace/executed-notebook/. executed-notebook/ >/dev/null 2>&1 || true
	@docker cp notebook-test-runner-ci:/workspace/validation-reports/. validation-reports/ >/dev/null 2>&1 || true
	@docker logs notebook-test-runner-ci > "$(VALIDATION_LOG_DIR)/dbservice-notebook-runner.log" 2>&1 || true
	@DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" $(NOTEBOOK_TEST_COMPOSE) ps > "$(VALIDATION_LOG_DIR)/dbservice-notebook-test-containers.log" 2>&1 || true
	@DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" $(NOTEBOOK_TEST_COMPOSE) logs > "$(VALIDATION_LOG_DIR)/dbservice-notebook-test.log" 2>&1 || true
	@if [ "$(VERBOSE)" = "1" ]; then printf '%s\n' '-> Removing validation runner container'; fi
	@docker rm -f notebook-test-runner-ci >/dev/null 2>&1 || true
	@$(VALIDATION_STEP) "Stopping DB Service containers and removing volumes" "$(VALIDATION_LOG_DIR)/dbservice-teardown.log" \
		env DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" \
		$(NOTEBOOK_TEST_COMPOSE) down -v --remove-orphans

validate:
	@printf '\nDB SERVICE VALIDATION\n'
	@printf '%s\n' '====================='
	@case "$(TEST_SUITE)" in \
		notebooks|spec_samples|client_readmes) ;; \
		*) \
			printf 'Unsupported test suite: %s\n' "$(TEST_SUITE)" >&2; \
			exit 2; \
			;; \
	esac
	@normalized_filter=$$(printf '%s' "$(TEST_FILTER)" | tr '[:upper:]' '[:lower:]'); \
	case "$$normalized_filter" in \
		""|curl|curl_notebook|curl_notebook.ipynb|python|python_notebook|python_notebook.ipynb|q|qclient|q_client|q-client|qclient_notebook|qclient_notebook.ipynb) ;; \
		*) \
			printf 'Unsupported TEST_FILTER: %s\n' "$(TEST_FILTER)" >&2; \
			printf '%s\n' 'Use curl, python, or qClient.' >&2; \
			exit 2; \
			;; \
	esac
	@mkdir -p "$(VALIDATION_LOG_DIR)"
	@: > "$(VALIDATION_LOG_DIR)/dbservice-setup.log"
	@: > "$(VALIDATION_LOG_DIR)/dbservice-teardown.log"
	@validation_suite="$(TEST_SUITE)"; \
	$(MAKE) dbservice-clean-containers; \
	$(MAKE) dbservice-up DBSERVICE_ENDPOINT="$(DBSERVICE_ENDPOINT)" || exit $$?; \
	trap '$(MAKE) dbservice-down' EXIT; \
	TEST_SUITE="$$validation_suite" TEST_SOURCE="$(TEST_SOURCE)" TEST_FILTER="$(TEST_FILTER)" FAIL_FAST="$(FAIL_FAST)" VERBOSE="$(VERBOSE)" VALIDATION_LOG_DIR="$(VALIDATION_LOG_DIR)" CODE_PROFILE="$(CODE_PROFILE)" CODE_DBS="$(CODE_DBS)" CODE_QCLIENT="$(CODE_QCLIENT)" CODE_PYCLIENT="$(CODE_PYCLIENT)" DS_REGISTRY="$(DS_REGISTRY)" DS_TAG="$(DS_TAG)" DS_RT_TAG="$(DS_RT_TAG)" NOTEBOOK_TEST_COMPOSE='$(NOTEBOOK_TEST_COMPOSE)' DBSERVICE_Q_CLIENT_LOCAL_DIR="$(DBSERVICE_Q_CLIENT_LOCAL_DIR)" DBSERVICE_Q_CLIENT_DIR="$(DBSERVICE_Q_CLIENT_DIR)" DBSERVICE_Q_CLIENT_URL="$(DBSERVICE_Q_CLIENT_URL)" DBSERVICE_Q_CLIENT_REF="$(DBSERVICE_Q_CLIENT_REF)" DBSERVICE_Q_CLIENT_SIBLING_DIR="$(DBSERVICE_Q_CLIENT_SIBLING_DIR)" DBSERVICE_PYTHON_CLIENT_DIR="$(DBSERVICE_PYTHON_CLIENT_DIR)" DBSERVICE_PYTHON_CLIENT_MODE="$(DBSERVICE_PYTHON_CLIENT_MODE)" DBSERVICE_PYTHON_CLIENT_URL="$(DBSERVICE_PYTHON_CLIENT_URL)" DBSERVICE_PYTHON_CLIENT_REF="$(DBSERVICE_PYTHON_CLIENT_REF)" DBSERVICE_PYTHON_CLIENT_SIBLING_DIR="$(DBSERVICE_PYTHON_CLIENT_SIBLING_DIR)" sh test/validation/run_compose_validation.sh
