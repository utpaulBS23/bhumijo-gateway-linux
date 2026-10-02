#!/usr/bin/env python3
"""Dummy QR validation API for testing the node before the real backend exists.

    python3 dev/dummy_qr_api.py                  # 127.0.0.1:8090
    python3 dev/dummy_qr_api.py --host 0.0.0.0   # reachable from the LAN
    python3 dev/dummy_qr_api.py --delay 5        # slow backend -> node falls back to cache

Then in the node's .env:

    QR_API_URL=http://127.0.0.1:8090/qr/validate
    QR_API_AUTH=0

Approves the codes in dev/qr/ named DUMMY-ALLOW-*, denies everything else.
Matches the node's defaults: reads the code from "token" (or card_id/code/
SCode), replies {"allowed": true|false}. Every request is printed so you can
see exactly what the Pi sends.

TEST ONLY. It approves well-known codes, so never point a live site at it.
"""

import argparse
import json
import time

from flask import Flask, jsonify, request

ALLOWED = {"DUMMY-ALLOW-0001", "DUMMY-ALLOW-0002", "DUMMY-ALLOW-0003"}
FIELDS = ("token", "card_id", "cardid", "code", "SCode")


def create_app(allowed=ALLOWED, delay=0.0):
    app = Flask("dummy_qr_api")

    @app.route("/qr/validate", methods=["GET", "POST"])
    def validate():
        data = request.get_json(silent=True) or {}
        data = {**request.args.to_dict(), **data}
        token = next((str(data[f]) for f in FIELDS if data.get(f)), None)
        if delay:
            time.sleep(delay)
        ok = token in allowed
        print(f"{request.method} from {request.remote_addr} "
              f"auth={request.headers.get('Authorization', '-')} "
              f"body={json.dumps(data)} -> {'ALLOW' if ok else 'DENY'}", flush=True)
        return jsonify(allowed=ok, token=token, facility=data.get("facility"),
                       door=data.get("door"))

    @app.get("/health")
    def health():
        return jsonify(ok=True, allowed=sorted(allowed))

    return app


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--delay", type=float, default=0.0, help="seconds to wait before replying")
    p.add_argument("--allow", action="append", help="extra code to approve (repeatable)")
    args = p.parse_args()
    allowed = ALLOWED | set(args.allow or [])
    print(f"Dummy QR API on http://{args.host}:{args.port}/qr/validate  approves: "
          f"{', '.join(sorted(allowed))}")
    create_app(allowed, args.delay).run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
