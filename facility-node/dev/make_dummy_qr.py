#!/usr/bin/env python3
"""Generate the dummy test QR images in dev/qr/.

    python3 dev/make_dummy_qr.py

DUMMY-ALLOW-*  approved by dev/dummy_qr_api.py
DUMMY-DENY-*   denied by it (and by every real backend)

Show them at a scanner to exercise the online-validation path. They open
nothing unless the node's QR_API_URL points at the dummy API.
"""

import os

import qrcode
from qrcode.image.pure import PyPNGImage

CODES = ["DUMMY-ALLOW-0001", "DUMMY-ALLOW-0002", "DUMMY-ALLOW-0003", "DUMMY-DENY-0001"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qr")


def make(code, out_dir=OUT):
    os.makedirs(out_dir, exist_ok=True)
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=12, border=4, image_factory=PyPNGImage)
    qr.add_data(code)
    qr.make(fit=True)
    path = os.path.join(out_dir, f"{code}.png")
    qr.make_image().save(path)
    return path


if __name__ == "__main__":
    for code in CODES:
        print(make(code))
