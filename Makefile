.PHONY: up down db-reset migrate test contract contract-ts lint-spec

up:          ; docker compose up -d --wait db
down:        ; docker compose down
db-reset:    ; docker compose down -v && $(MAKE) up && $(MAKE) migrate
migrate:     ; python -m db.migrate
test:        ; pytest -q
contract:    ; python -m contracts.tools.validate_fixtures
contract-ts: ; python -m contracts.tools.gen_ts_types
lint-spec:   ; python -m openapi_spec_validator contracts/openapi.yaml
