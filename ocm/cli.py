import argparse
import logging

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
        print(resolve_join_code(s))
    elif a.cmd == "check-ledger":
        import json
        from . import ledger
        from .db import connect
        report = ledger.consistency_report(connect(s.database_path))
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["ok"] else 1)
    else:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        app = create_app(s)
        print(f"Join path: /join/{app.state.join_code}", flush=True)
        # One worker only: live sockets are owned by this process. Trust X-Forwarded-* from the local tunnel.
        uvicorn.run(app, host=s.host, port=s.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                    ws_max_size=1_000_000, log_level="info")


if __name__ == "__main__":
    main()
