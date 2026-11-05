"""Moving the camera for the editor: held arrows, goto a position, and the editing pause.

Cameras like Reolink have no absolute move, so goto drives towards the target and stops early by
the overshoot it learned. Short corrections are timed pulses, sized by Calibrate moves.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import jsonfile
from .adapters import PTZ

log = logging.getLogger(__name__)

FINE = 3                     # speed for corrections
LEAD0 = {20: 150, 10: 90}    # starting overshoot guess per speed (0.4 s reads + coast), then learned
PULSE0 = {"pan": (-8, 65), "tilt": (11, 47)}  # units a fine pulse moves: a + b * seconds held, until Calibrate has run
HOLD = 1.0                   # s: a held arrow stops this long after the last keepalive (page closed, network gone)
PAUSE = 600                  # s: tracking and guard return come back this long after the last editor action
MAX_PULSE = 5                # s: the longest fine pulse, whatever the pulse table says
MIN_RATE = 5                 # units per second a fine pulse must move for Calibrate to keep the result
AXES = {"pan": 0, "tilt": 1}


class Busy(Exception):
    """The camera is still on a goto or calibration."""


class Learned:
    """What goto learned (overshoot per axis and speed) and Calibrate measured (pulse sizes), for all cameras.

    Kept in one JSON file across restarts.
    """

    def __init__(self, path: Path):
        self.path = path
        d = json.loads(path.read_text()) if path.exists() else {}
        self._lead: dict[str, float] = d.get("lead", {})    # "camera/axis/speed" -> units
        self._pulse: dict[str, list] = d.get("pulse", {})   # "camera/axis" -> [a, b]
        self._lock = threading.Lock()

    def lead(self, camera: str, axis: str, speed: int) -> float:
        return self._lead.get(f"{camera}/{axis}/{speed}", LEAD0[speed])

    def learn_lead(self, camera: str, axis: str, speed: int, value: float) -> None:
        if value > 3 * LEAD0[speed]:
            return  # the camera's own tracking or guard return moved it; not a coast
        key = f"{camera}/{axis}/{speed}"
        with self._lock:
            self._lead[key] = value if key not in self._lead else (self._lead[key] + value) / 2

    def pulse(self, camera: str, axis: str) -> tuple[float, float]:
        return tuple(self._pulse.get(f"{camera}/{axis}", PULSE0[axis]))

    def set_pulse(self, camera: str, axis: str, a: float, b: float) -> None:
        with self._lock:
            self._pulse[f"{camera}/{axis}"] = [a, b]

    def calibrated(self, camera: str) -> bool:
        return f"{camera}/pan" in self._pulse

    def save(self) -> None:
        with self._lock:
            jsonfile.write(self.path, {"lead": self._lead, "pulse": self._pulse}, indent=1)


class Pauses:
    """Per camera, what to restore when the editing pause ends, and when (wall clock). Kept across restarts."""

    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict[str, Any]] = json.loads(path.read_text()) if path.exists() else {}
        self._lock = threading.Lock()

    def get(self, camera: str) -> dict[str, Any] | None:
        """{"restore": ..., "until": ...}, or None when the camera is not paused."""
        with self._lock:
            entry = self._entries.get(camera)
            return dict(entry) if entry else None

    def start(self, camera: str, restore: Any, until: float) -> None:
        with self._lock:
            self._entries[camera] = {"restore": restore, "until": until}
            self._save()

    def extend(self, camera: str, until: float) -> None:
        with self._lock:
            if camera in self._entries:
                self._entries[camera]["until"] = until
                self._save()

    def end(self, camera: str) -> None:
        with self._lock:
            self._entries.pop(camera, None)
            self._save()

    def _save(self) -> None:
        jsonfile.write(self.path, self._entries)


class Motion:
    """One camera's moves for the editor. Only one goto or calibration runs at a time."""

    def __init__(self, name: str, camera: PTZ, learned: Learned, pauses: Pauses):
        self.name, self.camera, self.learned, self.pauses = name, camera, learned, pauses
        self._held: tuple[str, int, float] | None = None  # op, speed, deadline (monotonic)
        self._busy = threading.Lock()
        self._pausing = threading.Lock()  # one suspend or restore at a time
        self._restore_err: str | None = None

    def _say(self, msg: str, *args: Any) -> None:
        log.info("[%s] editor: " + msg, self.name, *args)

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    # --- held arrows and the pause, kept alive by the page ----------------------------------

    def hold(self, op: str, speed: int) -> None:
        """Start or keep alive a continuous move; tick() stops it when keepalives stop."""
        if not self._held or self._held[:2] != (op, speed):
            self.camera.move(op, speed)
        self._held = (op, speed, time.monotonic() + HOLD)

    def release(self) -> None:
        self._held = None
        self.camera.move("Stop")

    def pause(self) -> None:
        """Tracking and guard return off while editing, or the pause extended. What to restore goes to disk."""
        with self._pausing:
            if self.paused:
                self.pauses.extend(self.name, time.time() + PAUSE)  # a restart keeps the extended end
                return
            self.pauses.start(self.name, self.camera.suspend(), time.time() + PAUSE)
            self._say("tracking and guard return off")

    def resume(self, due: bool = False) -> None:
        """End the pause now, or with due only if it ran out (an editor action may just have extended it)."""
        with self._pausing:
            entry = self.pauses.get(self.name)
            if entry is None or (due and time.time() <= entry["until"]):
                return
            self.camera.restore(entry["restore"])
            self.pauses.end(self.name)
            self._say("tracking and guard return back")

    @property
    def paused(self) -> bool:
        return self.pauses.get(self.name) is not None

    def paused_for(self) -> int:
        """Seconds left of the pause, 0 when not paused."""
        entry = self.pauses.get(self.name)
        return round(entry["until"] - time.time()) if entry else 0

    def tick(self) -> None:
        """Stop a held arrow nobody keeps alive, and end a pause that ran out. Called a few times a second."""
        held = self._held
        if held and time.monotonic() > held[2]:
            try:
                self.release()
            except Exception as e:  # noqa: BLE001 - an adapter can raise anything
                self._say("stop failed: %s", type(e).__name__)
        entry = self.pauses.get(self.name)
        if entry and time.time() > entry["until"]:
            try:
                self.resume(due=True)
                self._restore_err = None
            except Exception as e:  # noqa: BLE001 - an adapter can raise anything
                if type(e).__name__ != self._restore_err:  # retried every tick; log only a new error
                    self._say("restore failed, retrying: %s", type(e).__name__)
                    self._restore_err = type(e).__name__

    # --- goto and calibration, each in its own thread ---------------------------------------

    def start_goto(self, pan: int, tilt: int, tol: int) -> None:
        self._start(self.goto, pan, tilt, tol)

    def start_calibration(self) -> None:
        self._start(self.calibrate)

    def _start(self, job: Callable, *args: Any) -> None:
        if not self._busy.acquire(blocking=False):
            raise Busy
        self._held = None

        def run() -> None:
            try:
                job(*args)
            except Exception:
                log.exception("[%s] editor: %s failed", self.name, job.__name__)
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True).start()

    def goto(self, pan: int, tilt: int, tol: int = 12) -> None:
        """A fast pass (diagonal when both axes are far), then up to two fine corrections."""
        try:
            pos = settle(self.camera)
            for n in range(3):
                want, speed = self.plan(pos, pan, tilt, tol, fine=n > 0)
                if not want:
                    break
                pos = self.drive(pos, want, speed)
        finally:
            self.camera.move("Stop")

    def plan(self, pos: tuple[int, int], pan: int, tilt: int, tol: int, fine: bool) -> tuple[dict[str, int], int]:
        """Axes (signed units to go) and speed for the next pass; no axes when there."""
        want = {a: d for a, d in (("pan", pan - pos[0]), ("tilt", tilt - pos[1])) if abs(d) > tol}
        if not fine and want:
            speed = 20 if max(map(abs, want.values())) > 400 else 10
            fast = {a: d for a, d in want.items() if abs(d) > 2 * self.learned.lead(self.name, a, speed)}
            if fast:
                return fast, speed
        return want, FINE

    def drive(self, start: tuple[int, int], want: dict[str, int], speed: int) -> tuple[int, int]:
        """One pass towards want: each axis stops its learned overshoot early.

        A diagonal runs until one axis is due, then stops and carries on with the other. The fine
        speed is timed pulses instead: position reads lag too much to stop a slow move on them.
        """
        if speed == FINE:
            for a, d in want.items():
                pa, pb = self.learned.pulse(self.name, a)
                pulse(self.camera, _op(a, d), min(MAX_PULSE, max(0.1, (abs(d) - pa) / pb)))
            return settle(self.camera)
        target = {a: max(abs(d) - self.learned.lead(self.name, a, speed), 1) for a, d in want.items()}
        left, op, end = set(want), None, time.monotonic() + 15
        while left and time.monotonic() < end:
            new = "".join(_op(a, want[a]) for a in ("pan", "tilt") if a in left)
            if new != op:
                if op:
                    self.camera.move("Stop")  # brakes the axis that is due; a bare switch of direction lets it coast on
                self.camera.move(new, speed)
                op = new
            pos = self.camera.position()
            left = {a for a in left if abs(pos[AXES[a]] - start[AXES[a]]) < target[a]}
        self.camera.move("Stop")
        pos = settle(self.camera)
        for a in want:
            self.learned.learn_lead(self.name, a, speed, max(0, abs(pos[AXES[a]] - start[AXES[a]]) - target[a]))
        self.learned.save()
        return pos

    def calibrate(self) -> None:
        """What a fine pulse moves per axis, a + b * seconds: a short and a long pulse each way, averaged."""
        for a, i in AXES.items():
            moved = {}
            for seconds in (0.3, 1.0):
                got = []
                for d in (1, -1):
                    start = settle(self.camera)
                    pulse(self.camera, _op(a, d), seconds)
                    got.append(abs(settle(self.camera)[i] - start[i]))
                moved[seconds] = sum(got) / 2
            b = (moved[1.0] - moved[0.3]) / 0.7
            if b < MIN_RATE:
                self._say("calibration of %s kept the old values: the camera barely moved (%s)", a, moved)
                continue
            self.learned.set_pulse(self.name, a, round(moved[0.3] - 0.3 * b, 1), round(b, 1))
        self.learned.save()
        self._say("calibrated moves %s", {a: list(self.learned.pulse(self.name, a)) for a in AXES})


def pulse(camera: PTZ, op: str, seconds: float) -> None:
    """A timed move at the fine speed."""
    with camera.lock:  # nothing else talks to the camera until the Stop, or the pulse runs long
        camera.move(op, FINE)
        time.sleep(seconds)
        camera.move("Stop")


def settle(camera: PTZ) -> tuple[int, int]:
    """The position once two readings in a row agree (the camera coasts after Stop)."""
    last = None
    for _ in range(12):
        pos = camera.position()
        if pos == last:
            break
        last = pos
    return last


def _op(axis: str, d: float) -> str:
    """The move for a signed distance on one axis (tilt grows downwards)."""
    if axis == "pan":
        return "Right" if d > 0 else "Left"
    return "Down" if d > 0 else "Up"
