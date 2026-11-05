"""Keeps one camera's Frigate zone (and optional motion mask) in step with where the camera looks."""
from __future__ import annotations

import logging
import time
from collections import deque

from .adapters import PTZ
from .config import CameraConfig, ZoneModel
from .frigate import Frigate
from .geometry import predict
from .keyframes import Keyframe, KeyframeStore

log = logging.getLogger(__name__)

POLL = 0.5        # s between position reads
MIN_PUSH = 1.0    # s between zone writes while the camera moves
LOST_AFTER = 10   # s without a position before the zone is switched off
LAG = (1.0, 4.5)  # s Frigate's detect frames run behind the camera: measured 1-2.3 s one day, 3.2-4.5 s another
UNSET = object()  # the zone before the first write

Position = tuple[int, int]


def frame_positions(seen: deque, now: float, lag: tuple[float, float] = LAG) -> tuple[Position, ...]:
    """Every position the frame Frigate detects on now may be from, oldest first.

    seen holds (time, position) readings, oldest first; readings older than needed are dropped from it.
    While the camera stands still this is one position.
    """
    while len(seen) > 1 and seen[1][0] <= now - lag[1]:
        seen.popleft()
    return tuple(dict.fromkeys(p for t, p in seen if t <= now - lag[0])) or tuple(p for _, p in list(seen)[:1])


def sky_up(mask_tilt: int, pos: Position | None) -> bool:
    """True when the camera looks at or above mask_tilt (tilt grows downwards)."""
    return pos is not None and pos[1] <= mask_tilt


class Follower:
    def __init__(self, cfg: CameraConfig, model: ZoneModel, camera: PTZ, keyframes: KeyframeStore, frigate: Frigate):
        self.cfg, self.model, self.camera, self.keyframes, self.frigate = cfg, model, camera, keyframes, frigate
        self.zone: object = UNSET  # what Frigate has now: coordinates, or None when off
        self._kfs: list[Keyframe] = []
        self._kf_mtime: float | None = None
        self._last_push, self._last_was = 0.0, None
        self._last_ok = time.monotonic()
        self._last_err: str | None = None
        self._lost = False  # no position for LOST_AFTER
        self._masked: bool | None = None
        self._walled = False
        self._seen: deque = deque()

    def _say(self, msg: str) -> None:
        log.info("[%s] %s", self.cfg.name, msg)

    def run(self) -> None:
        last = None
        while True:
            try:
                self.step()
                last = None
            except Exception as e:
                if repr(e) != last:  # the same failure repeats every few seconds, e.g. while Frigate is down
                    log.exception("[%s] error", self.cfg.name)
                    last = repr(e)
                time.sleep(5)
            time.sleep(POLL)

    def step(self) -> None:
        pos = self._read()
        if self._lost:
            self._seen.clear()  # readings from before the gap say nothing about the frames after it
            self._switch_off()
            return
        if self.cfg.pan_range and pos is not None:
            self._keep_in_range(pos)
        # zone and mask follow the view Frigate is detecting on, not the camera's live one
        now = time.monotonic()
        if pos is not None:
            self._seen.append((now, pos))
        was = frame_positions(self._seen, now)
        self._reload_keyframes()
        if was and was != self._last_was and now - self._last_push >= MIN_PUSH:
            self._push_zone(was)
        if self.cfg.mask:
            self._push_mask(was[0] if was else None)

    def _read(self) -> Position | None:
        try:
            pos = self.camera.position()
        except Exception as e:  # noqa: BLE001 - an adapter can raise anything
            if str(e) != self._last_err:  # don't repeat the same error twice a second
                self._say(f"camera: {e}")
                self._last_err = str(e)
            self._lost = time.monotonic() - self._last_ok > LOST_AFTER
            return None
        self._last_ok, self._last_err, self._lost = time.monotonic(), None, False
        return pos

    def _keep_in_range(self, pos: Position) -> None:
        """A glitch can leave the camera facing the wall until guard return, blind to what it watches.

        Home is sent on every read: one sent while the camera still moves is ignored. One more goes on
        the first read back in range, in case a stop cut the trip home short.
        """
        lo, hi = self.cfg.pan_range
        out = not lo <= pos[0] <= hi
        if out or self._walled:
            self.camera.home()
            if not self._walled:
                self._say(f"{pos} -> out of range, sent home")
        self._walled = out

    def _reload_keyframes(self) -> None:
        mtime = self.keyframes.mtime()
        if mtime != self._kf_mtime:
            self._kfs, self._kf_mtime = self.keyframes.load(), mtime
            self._say(f"loaded {len(self._kfs)} keyframes")
            self._last_was = None  # recompute with the new keyframes

    def _push_zone(self, was: tuple[Position, ...]) -> None:
        coords = predict(self.model, *was[0], self._kfs, more=was[1:])
        if coords != self.zone:
            self.frigate.set_zone(self.cfg.name, self.cfg.zone, coords)
            self._say(f"{was[0] if len(was) == 1 else was} -> {coords or 'off'}")
            self.zone = coords
        self._last_was, self._last_push = was, time.monotonic()

    def _switch_off(self) -> None:
        """Zone and mask off while the position is unknown: more motion to check beats a blind spot."""
        if self.zone is not None:
            self.frigate.set_zone(self.cfg.name, self.cfg.zone, None)
            self._say("position unknown -> off")
            self.zone, self._last_was = None, None
        if self.cfg.mask and self._masked is not False:
            self.frigate.set_mask(self.cfg.name, self.cfg.mask, False)
            self._say("position unknown -> mask off")
            self._masked = False

    def _push_mask(self, pos: Position | None) -> None:
        if pos is None and self._masked is not None:
            return  # no reading yet this time; the mask stays as it is
        on = sky_up(self.cfg.mask_tilt, pos)
        if on != self._masked:
            self.frigate.set_mask(self.cfg.name, self.cfg.mask, on)
            self._say(f"{pos} -> mask {'on' if on else 'off'}")
            self._masked = on
