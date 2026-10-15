"""Camera monitoring: is the IP camera reachable, is the feed live, snapshots.

    camera.py    TCP probe, frozen-feed check, JPEG snapshots for door alerts

Switched by ENABLE_CAMERA; set up with `facility camera`. Never in the unlock
path: a dead camera only means alerts go out without a picture.
"""
