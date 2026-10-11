.PHONY: install web dev serve test preflight tunnel check-ledger rust-agent

install:
	uv sync
	cd web && npm ci

web:
	scripts/build_web.sh

dev:
	scripts/dev.sh

serve:
	uv run ocm serve

test:
	uv run pytest -q

preflight:
	scripts/demo_preflight.sh

tunnel:
	scripts/demo_tunnel.sh

check-ledger:
	uv run ocm check-ledger

rust-agent:
	cargo build --release --manifest-path crates/ocm-node/Cargo.toml
