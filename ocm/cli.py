import argparse
import logging
from pathlib import Path

import uvicorn

from .app import create_app, resolve_join_code
from .config import Settings


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ocm", description="OpenComputeMesh coordinator")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="Run the coordinator (single process)")
    sub.add_parser("join-code", help="Print the network join code")
    sub.add_parser("check-ledger", help="Verify ledger invariants")
    a = p.parse_args(argv)
    s = Settings.from_env()
    if a.cmd == "join-code":
        # Never generate here: a fresh code under the wrong settings would not match the running coordinator.
        if not s.join_code and not (Path(s.data_dir) / "join_code").exists():
            raise SystemExit(f"No join code under OCM_DATA_DIR={s.data_dir} and OCM_JOIN_CODE is unset: the coordinator "
                             "has not created one with these settings. Run `ocm serve` first, or use its settings.")
        print(resolve_join_code(s))
    elif a.cmd == "check-ledger":
        import json
        from . import ledger
        from .db import connect
        if not Path(s.database_path).is_file():  # connect() would create an empty database and report it consistent
            raise SystemExit(f"No database at OCM_DATABASE_PATH={s.database_path}; check the coordinator's settings.")
        report = ledger.consistency_report(connect(s.database_path))
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["ok"] else 1)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        app = create_app(s)
        print(f"Join path: /join/{app.state.join_code}", flush=True)
        # One worker only: live sockets are owned by this process. Trust X-Forwarded-* from the local tunnel.
        # No access log: request lines carry the join code (/join/<code>) and WebSocket tickets (?ticket=).
        uvicorn.run(app, host=s.host, port=s.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                    ws_max_size=1_000_000, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
