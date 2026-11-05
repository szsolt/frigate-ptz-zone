"""Zone geometry: where a zone drawn at one camera position lands at another. No I/O.

A keyframe's zone is moved to the new view (shifted, or rotated through the lens), and only the area
where the nearest keyframes in range all agree is kept: a missed strip is cheaper than a zone that
spills onto the street.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence

import cv2
import numpy as np

from .config import Lens, ZoneModel
from .keyframes import Keyframe

Point = Sequence[float]
Side = tuple[Callable[[Point], bool], Callable[[Point, Point], Point]]
EDGE = 0.02  # a drawn point this close to the frame edge means "the zone continues off-screen"


def cut(pts: list, sides: Iterable[Side]) -> list:
    """Sutherland-Hodgman: the part of the polygon inside every side (an inside test and an edge cutter)."""
    for inside, cross in sides:
        out = []
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            if inside(b):
                if not inside(a):
                    out.append(cross(a, b))
                out.append(b)
            elif inside(a):
                out.append(cross(a, b))
        pts = out
        if not pts:
            break
    return pts


def clip(poly: np.ndarray, w: float, h: float) -> np.ndarray:
    """The polygon clipped to the frame rectangle."""
    def at_x(x):
        return lambda a, b: (x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))

    def at_y(y):
        return lambda a, b: (a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]), y)

    sides = ((lambda p: p[0] >= 0, at_x(0)), (lambda p: p[0] <= w, at_x(w)),
             (lambda p: p[1] >= 0, at_y(0)), (lambda p: p[1] <= h, at_y(h)))
    return np.float32(cut([tuple(p) for p in poly], sides))


def _edges(p: Point) -> set[str]:
    return {e for e, hit in (("l", p[0] <= EDGE), ("r", p[0] >= 1 - EDGE),
                             ("t", p[1] <= EDGE), ("b", p[1] >= 1 - EDGE)) if hit}


def extend_off_frame(norm: np.ndarray) -> np.ndarray:
    """Move each point drawn on the frame edge a frame-width further out.

    It goes along the line from a neighbour off its frame edge, so the zone's edge keeps its slope.
    Corners between two neighbours on the same frame edges just go straight out.
    """
    out = norm.copy()
    n = len(norm)
    for i, p in enumerate(norm):
        if not _edges(p):
            continue
        # a neighbour on another frame edge counts too: the line between them cuts across the frame
        neighbours = sorted((norm[(i - 1) % n], norm[(i + 1) % n]), key=lambda x: bool(_edges(x)))
        q = next((x for x in neighbours if not _edges(x) & _edges(p)), None)
        if q is not None:
            d = p - q
            out[i] = p + d / np.linalg.norm(d)
        else:
            out[i] = np.where(p <= EDGE, -1.0, np.where(p >= 1 - EDGE, 2.0, p))
    return out


def raster(poly: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    """The polygon (grid px) filled on the grid, as a boolean image."""
    w, h = grid
    m = np.zeros((h, w), np.uint8)
    if len(poly) >= 3:
        cv2.fillPoly(m, [np.int32(poly)], 255)
    return m > 0


def drawn(coords: Sequence[float], grid: tuple[int, int]) -> np.ndarray:
    """A zone as drawn (normalised coordinates), filled on the grid with its off-screen edges extended."""
    w, h = grid
    return raster(clip(extend_off_frame(np.float32(coords).reshape(-1, 2)) * [w, h], w, h), grid)


def shift(model: ZoneModel, k: Keyframe, pan: float, tilt: float) -> np.ndarray:
    """Grid px the keyframe's picture moves when the camera turns from the keyframe to (pan, tilt)."""
    ax, bx, ay, by = model.shift
    dp, dt = pan - k.pan, tilt - k.tilt
    return np.array([ax * dp + bx * dt, ay * dp + by * dt])


def rotation(lens: Lens, pan: float, tilt: float) -> np.ndarray:
    """Camera-from-world rotation: pan about the vertical, then tilt about the camera's own x axis."""
    a, b = math.radians(lens.pan * pan), math.radians(lens.tilt * tilt)
    ry = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    rx = np.array([[1, 0, 0], [0, math.cos(b), -math.sin(b)], [0, math.sin(b), math.cos(b)]])
    return rx @ ry


def warp(model: ZoneModel, k: Keyframe, pan: float, tilt: float) -> np.ndarray:
    """The keyframe's zone as seen from (pan, tilt), clipped to the frame, in grid px."""
    w, h = model.grid
    poly = extend_off_frame(np.float32(k.coords).reshape(-1, 2)) * [w, h]
    lens = model.lens
    if lens is None:
        return clip(poly + shift(model, k, pan, tilt), w, h)
    f, k1 = lens.f, lens.k1
    turn = rotation(lens, pan, tilt) @ rotation(lens, k.pan, k.tilt).T
    rays = []
    for x, y in poly:
        u, v = (x - w / 2) / f, (y - h / 2) / f
        r2 = u * u + v * v
        for _ in range(5):  # undo the barrel distortion, seen = true * (1 + k1 r²), by iteration
            s = 1 + k1 * r2
            r2 = (u * u + v * v) / (s * s)
        rays.append(turn @ np.array([u / s, v / s, 1.0]))
    # clip against the planes through the lens and the frame edges, so nothing behind the camera is projected
    normals = (np.array([1, 0, w / 2 / f]), np.array([-1, 0, w / 2 / f]),
               np.array([0, 1, h / 2 / f]), np.array([0, -1, h / 2 / f]))
    sides = [(lambda p, n=n: n @ p >= 0, lambda a, b, n=n: a + (b - a) * (n @ a) / (n @ a - n @ b)) for n in normals]
    out = []
    for ray in cut(rays, sides):
        u, v = ray[0] / ray[2], ray[1] / ray[2]
        s = 1 + k1 * (u * u + v * v)
        out.append((u * s * f + w / 2, v * s * f + h / 2))
    return np.float32(out)


def predict(model: ZoneModel, pan: float, tilt: float, kfs: Sequence[Keyframe],
            more: Iterable[tuple[float, float]] = ()) -> str | None:
    """The zone at this position as Frigate wants it ("x,y,x,y,..." normalised), or None when off."""
    poly = predict_points(model, pan, tilt, kfs, more)
    # Frigate rejects "1.000"; format like its UI does
    return None if poly is None else ",".join(f"{round(float(v), 3):g}" for v in poly.ravel())


def predict_points(model: ZoneModel, pan: float, tilt: float, kfs: Sequence[Keyframe],
                   more: Iterable[tuple[float, float]] = ()) -> np.ndarray | None:
    """The zone at this position as normalised (x, y) points, or None when off.

    It is off when the position is too far from every keyframe. With `more` positions, the zone
    is only where it holds at every one of them: while the camera moves, the frame Frigate is
    detecting on may be from any of them.
    """
    w, h = model.grid
    use = []
    for p, t in [(pan, tilt), *more]:
        near = sorted((float(np.linalg.norm(shift(model, k, p, t))), i) for i, k in enumerate(kfs))[:model.agree]
        polys = [warp(model, kfs[i], p, t) for d, i in near if d <= model.max_dist]
        if not polys:
            return None
        use += polys
    m = np.full((h, w), True)
    for poly in use:
        m &= raster(poly, model.grid)
    contours, _ = cv2.findContours(m.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    poly = cv2.approxPolyDP(max(contours, key=cv2.contourArea), 3, True).reshape(-1, 2)
    if len(poly) < 3:
        return None
    return np.clip(poly / [w, h], 0, 1)


def outermost(k: Keyframe, rest: Sequence[Keyframe]) -> bool:
    """True when the keyframe lies outside the pan/tilt box of the others."""
    return not rest or not (min(r.pan for r in rest) <= k.pan <= max(r.pan for r in rest)
                            and min(r.tilt for r in rest) <= k.tilt <= max(r.tilt for r in rest))


def agreement(model: ZoneModel, kfs: Sequence[Keyframe]) -> list[float]:
    """Per keyframe, 0..1: how well the others predict its drawn zone (overlap over union).

    Low means it disagrees with its neighbours: redraw it, or add a keyframe between.
    Outermost keyframes score 1: they could only be compared with an extrapolation.
    """
    out = []
    for i, k in enumerate(kfs):
        rest = [*kfs[:i], *kfs[i + 1:]]
        if outermost(k, rest):
            out.append(1.0)
            continue
        out.append(round(overlap(model, k, rest), 2))
    return out


def overlap(model: ZoneModel, k: Keyframe, rest: Sequence[Keyframe], mask: np.ndarray | None = None) -> float:
    """0..1, overlap over union of the keyframe's drawn zone (mask, if already filled) and what rest predicts."""
    a = drawn(k.coords, model.grid) if mask is None else mask
    guess = predict_points(model, k.pan, k.tilt, rest)
    if guess is None:
        return 0.0
    b = raster(guess * model.grid, model.grid)
    return float((a & b).sum() / max((a | b).sum(), 1))


def thin(model: ZoneModel, kfs: Sequence[Keyframe], keep: float = 0.95) -> list[Keyframe]:
    """Drop keyframes the others reproduce to `keep` or better, one at a time, most redundant first.

    Outermost keyframes stay: nothing could stand in for them.
    """
    kfs = list(kfs)
    while True:
        scores = agreement(model, kfs)
        drop = [(v, i) for i, v in enumerate(scores) if v >= keep and not outermost(kfs[i], [*kfs[:i], *kfs[i + 1:]])]
        if not drop:
            return kfs
        kfs.pop(max(drop)[1])


def centre_offset(model: ZoneModel, x: float, y: float) -> tuple[float, float]:
    """The pan/tilt turn that brings a point of the frame (0..1) to the middle, from the shift fit."""
    ax, bx, ay, by = model.shift
    gw, gh = model.grid
    ox, oy = (x - 0.5) * gw, (y - 0.5) * gh
    det = ax * by - bx * ay
    # turning by (dp, dt) shifts the picture by shift . (dp, dt); the point must shift by -offset
    return (-ox * by + oy * bx) / det, (-oy * ax + ox * ay) / det


def coverage_map(model: ZoneModel, kfs: Sequence[Keyframe], pan_range: tuple[int, int] | None,
                 steps: tuple[int, int] = (24, 12)) -> dict:
    """Pan/tilt cells where the zone would be off, cells where it is extrapolated, and the keyframe scores."""
    pans, tilts = [k.pan for k in kfs] or [0], [k.tilt for k in kfs] or [0]
    lo, hi = pan_range or (min(pans) - 300, max(pans) + 300)
    box = [lo, hi, max(0, min(tilts) - 150), max(tilts) + 150]
    nx, ny = steps
    cells = [(i, j, box[0] + (i + .5) * (box[1] - box[0]) / nx, box[2] + (j + .5) * (box[3] - box[2]) / ny)
             for j in range(ny) for i in range(nx)]
    off = [[i, j] for i, j, pan, tilt in cells if predict(model, pan, tilt, kfs) is None]
    # beyond the outermost keyframe the zone is extrapolated; a keyframe there pins it down
    extrapolated = [[i, j] for i, j, pan, tilt in cells if [i, j] not in off
                    and not (min(pans) <= pan <= max(pans) and min(tilts) <= tilt <= max(tilts))]
    return {"box": box, "steps": list(steps), "off": off, "extrapolated": extrapolated, "fit": agreement(model, kfs)}
