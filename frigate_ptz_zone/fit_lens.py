"""Fit a camera's lens from its keyframes, and print the "lens" entry for cameras.json.

usage: python3 -m frigate_ptz_zone.fit_lens CAMERA [KEYFRAMES.json]

Each keyframe is predicted from its nearest neighbour alone; the lens that best reproduces the drawn
zones wins (overlap over union, averaged). Needs scipy (pip install .[fit]).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

from . import geometry
from .config import Lens, Settings, ZoneModel, load_cameras
from .keyframes import Keyframe, KeyframeStore

KEYS = ("f", "pan", "tilt", "k1")


def score(model: ZoneModel, kfs: list[Keyframe], drawn: list[np.ndarray]) -> float:
    """Mean overlap over union of each drawn zone with what the other keyframes predict."""
    return float(np.mean([geometry.overlap(model, k, kfs[:i] + kfs[i + 1:], drawn[i]) for i, k in enumerate(kfs)]))


def fit(model: ZoneModel, kfs: list[Keyframe]) -> tuple[Lens, float, float]:
    """The best lens, its score and the shift model's score."""
    model = replace(model, agree=1, max_dist=float("inf"), lens=None)
    drawn = [geometry.drawn(k.coords, model.grid) for k in kfs]

    def cost(x) -> float:
        return -score(replace(model, lens=Lens(*x)), kfs, drawn)

    opts = {"method": "Nelder-Mead", "options": {"xatol": 1e-3, "fatol": 1e-4, "maxiter": 300}}
    w = model.grid[0]
    ax, _, _, by = model.shift
    best = None
    # start from the shift fit: degrees per unit = shift px per unit over f, at a few f
    for f in (w * 0.4, w * 0.6, w * 0.8, w * 1.0, w * 1.3):
        r = minimize(cost, [f, -np.degrees(ax / f), -np.degrees(by / f), 0.0], **opts)
        if best is None or r.fun < best.fun:
            best = r
    for _ in range(2):  # Nelder-Mead stalls; restarting from the best point gets it moving again
        best = minimize(cost, best.x, **opts)
    lens = Lens(*(round(float(v), 4) for v in best.x))
    return lens, -best.fun, score(model, kfs, drawn)


def main() -> None:
    parser = argparse.ArgumentParser(prog="fit_lens", description=__doc__.split("\n")[0])
    parser.add_argument("camera")
    parser.add_argument("keyframes", nargs="?", type=Path, help="default: the camera's keyframes file")
    args = parser.parse_args()
    cfg = load_cameras(Settings.from_env())[args.camera]
    if cfg.model is None:
        sys.exit(f"{args.camera} has no shift yet: run python3 -m frigate_ptz_zone.fit_shift {args.camera} first")
    kfs = KeyframeStore(args.keyframes or cfg.keyframes).load()
    lens, lens_score, shift_score = fit(cfg.model, kfs)
    print(f"shift model: {shift_score:.3f}   lens model: {lens_score:.3f}   (mean overlap, {len(kfs)} keyframes)")
    print(f'"lens": {json.dumps(dict(zip(KEYS, (lens.f, lens.pan, lens.tilt, lens.k1))))}')


if __name__ == "__main__":
    main()
