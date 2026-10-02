#!/usr/bin/env python3
"""Create, list and revoke unlock QR codes stored on this Pi.

These are local codes (test, commissioning, staff), kept apart from the
backend's tokens so the 5-minute pull never removes them. Any valid code opens
either door. Codes expire after 24h unless you say otherwise.

Run on the Pi as the service user so the database stays writable:

    cd /opt/facility-node
    sudo -u facility ./venv/bin/python qr_tool.py create --label test
    sudo -u facility ./venv/bin/python qr_tool.py create --label cleaners --count 5 --hours 720
    sudo -u facility ./venv/bin/python qr_tool.py list
    sudo -u facility ./venv/bin/python qr_tool.py revoke --label test
"""

import argparse
import os
import re
import secrets
import sys
import time
from datetime import datetime

DEFAULT_DB = "/var/lib/facility/facility.db"
DEFAULT_OUT = "/var/lib/facility/qr"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def new_token():
    # "fn-" marks Pi-created codes; 24 random bytes = 192 bits, unguessable
    return "fn-" + secrets.token_urlsafe(24)


def fmt_time(ts):
    return "never" if ts is None else datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def write_qr(token, label, out_dir):
    """Save <out_dir>/<label>-<n>.png (and .svg); print the code in the terminal."""
    import qrcode
    from qrcode.image.pure import PyPNGImage
    from qrcode.image.svg import SvgPathImage

    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", label)
    stem = os.path.join(out_dir, f"{safe}-{token[3:11]}")

    def make(factory):
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                           box_size=12, border=4, image_factory=factory)
        qr.add_data(token)
        qr.make(fit=True)
        return qr

    make(PyPNGImage).make_image().save(stem + ".png")
    make(SvgPathImage).make_image().save(stem + ".svg")
    make(None).print_ascii(out=sys.stdout, invert=True)
    return stem + ".png"


def cmd_create(store, args):
    if args.no_expiry:
        print("WARNING: a code with no expiry works until revoked. Anyone who "
              "photographs it can open the doors.", file=sys.stderr)
        expires_at = None
    else:
        expires_at = time.time() + args.hours * 3600
    for _ in range(args.count):
        token = args.token or new_token()
        if not TOKEN_RE.match(token):
            sys.exit("token must be 16-128 chars of A-Z a-z 0-9 _ -")
        store.add_local_token(token, args.label, expires_at)
        path = write_qr(token, args.label, args.out)
        print(f"label:   {args.label}\ntoken:   {token}\nexpires: {fmt_time(expires_at)}\n"
              f"image:   {path}\n")


def cmd_list(store, args):
    rows = store.list_local_tokens()
    if not rows:
        print("no local codes")
        return
    now = time.time()
    print(f"{'label':<16} {'token':<14} {'expires':<17} status")
    for token, label, expires_at, _ in rows:
        status = "EXPIRED" if expires_at is not None and expires_at <= now else "active"
        print(f"{label:<16} {token[:11] + '...':<14} {fmt_time(expires_at):<17} {status}")


def cmd_revoke(store, args):
    n = store.revoke_local_tokens(token=args.token, label=args.label, everything=args.all,
                                  expired_before=time.time() if args.expired else None)
    print(f"revoked {n} code(s)")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=os.environ.get("DB_PATH", DEFAULT_DB))
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create unlock code(s) + QR images")
    c.add_argument("--label", required=True, help="who/what it's for, e.g. test, cleaners")
    c.add_argument("--hours", type=float, default=24, help="valid for N hours (default 24)")
    c.add_argument("--no-expiry", action="store_true", help="never expires (avoid)")
    c.add_argument("--count", type=int, default=1, help="how many codes")
    c.add_argument("--token", help="register this exact token instead of a random one")
    c.add_argument("--out", default=DEFAULT_OUT, help=f"image folder (default {DEFAULT_OUT})")

    sub.add_parser("list", help="show local codes")

    r = sub.add_parser("revoke", help="delete local codes")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--token")
    g.add_argument("--label")
    g.add_argument("--expired", action="store_true")
    g.add_argument("--all", action="store_true")

    args = p.parse_args(argv)
    if args.cmd == "create" and args.token and args.count != 1:
        p.error("--token works with --count 1 only")

    from store import Store
    store = Store(args.db)
    try:
        {"create": cmd_create, "list": cmd_list, "revoke": cmd_revoke}[args.cmd](store, args)
    finally:
        store.close()


if __name__ == "__main__":
    main()
