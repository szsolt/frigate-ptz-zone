"""Measure how far the picture moves per pan/tilt unit, and print "grid" and "shift" for cameras.json.

usage: python3 -m frigate_ptz_zone.fit_shift CAMERA

The camera pulses right, left, down and up. After each pulse, Frigate's snapshot is compared with the
one before (matched features), and the shift per unit is fitted to all four. Point the camera at a
scene with detail (not sky), with nothing moving close by. Takes about a minute.
"""
from __future__ import annotations

import argparse
import json
import time

import cv2
import numpy as np

from .adapters import open_camera
from .config import Settings, load_cameras
from .frigate import Frigate
from .motion import pulse, settle

HEIGHT = 540        # grid height in px; the width follows the camera's aspect
WAIT = 6            # s for Frigate's snapshot to catch up with the camera
PULSE = 1.5         # s per pulse, about 50-100 units
MOVES = ("Right", "Left", "Down", "Up")


def picture_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, agreement): how far the content of b sits from where it was in a, in px.

    Matches ORB features and fits one shift plus a small turn and zoom to them, ignoring matches that
    disagree (repeating patterns like fences, things that moved meanwhile). A pan also rolls a wide
    lens picture a little, so the shift is read at the centre. The top and bottom strips are left out:
    on-screen text there (clock, camera name) does not move with the picture.
    """
    h, w = a.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    mask[int(h * 0.1):int(h * 0.9)] = 255
    orb = cv2.ORB_create(3000)
    ka, da = orb.detectAndCompute(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), mask)
    kb, db = orb.detectAndCompute(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), mask)
    if da is None or db is None:
        return 0.0, 0.0, 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 3:
        return 0.0, 0.0, 0.0
    pa = np.float32([ka[m.queryIdx].pt for m in matches])
    pb = np.float32([kb[m.trainIdx].pt for m in matches])
    m, inliers = cv2.estimateAffinePartial2D(pa, pb, method=cv2.RANSAC, ransacReprojThreshold=3)
    if m is None:
        return 0.0, 0.0, 0.0
    centre = np.float32([w / 2, h / 2])
    dx, dy = m[:, :2] @ centre + m[:, 2] - centre
    return float(dx), float(dy), float(inliers.mean())


def solve(turns: list[tuple[int, int]], shifts: list[tuple[float, float]]) -> list[float]:
    """[ax, bx, ay, by] from (dpan, dtilt) turns and the (dx, dy) shifts they caused, least squares."""
    m, *_ = np.linalg.lstsq(np.float64(turns), np.float64(shifts), rcond=None)
    (ax, ay), (bx, by) = m
    return [round(float(v), 3) for v in (ax, bx, ay, by)]


def main() -> None:
    parser = argparse.ArgumentParser(prog="fit_shift", description=__doc__.split("\n")[0])
    parser.add_argument("camera")
    args = parser.parse_args()
    settings = Settings.from_env()
    cfg = load_cameras(settings)[args.camera]
    frigate = Frigate(settings.frigate_url)
    camera = open_camera(cfg.adapter, cfg.options)

    def snapshot() -> np.ndarray:
        time.sleep(WAIT)
        return cv2.imdecode(np.frombuffer(frigate.latest_jpg(cfg.name, HEIGHT), np.uint8), cv2.IMREAD_COLOR)

    state = camera.suspend()
    try:
        pos, img = settle(camera), snapshot()
        while (now := settle(camera)) != pos:  # a move tracking started may still be running
            pos, img = now, snapshot()
        turns, shifts = [], []
        for op in MOVES:
            pulse(camera, op, PULSE)
            new, new_img = settle(camera), snapshot()
            dx, dy, conf = picture_shift(img, new_img)
            turn = (new[0] - pos[0], new[1] - pos[1])
            print(f"{op:5} turned {turn[0]:+4} / {turn[1]:+4} units, picture moved {dx:+6.1f} / {dy:+6.1f} px"
                  f"{'  (unsure: little detail or something moved)' if conf < 0.2 else ''}")
            turns.append(turn)
            shifts.append((dx, dy))
            pos, img = new, new_img
    finally:
        camera.restore(state)
        camera.close()
    print(f'"grid": {json.dumps([img.shape[1], img.shape[0]])}, "shift": {json.dumps(solve(turns, shifts))}')


if __name__ == "__main__":
    main()
