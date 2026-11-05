"""The zone editor: a web page with the live view, PTZ buttons, a draggable zone and the coverage map.

No login: keep it on your LAN.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import geometry
from .adapters import OPS
from .config import ZoneModel
from .follower import UNSET
from .keyframes import Keyframe
from .motion import Busy

if TYPE_CHECKING:
    from .frigate import Frigate
    from .service import Rig

log = logging.getLogger(__name__)

PAGE = Path(__file__).with_name("editor.html")
NEAR = 40  # pan/tilt units: this close to a keyframe, edit that keyframe


class ApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Editor(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, rigs: dict[str, Rig], frigate: Frigate):
        super().__init__(("0.0.0.0", port), Handler)
        self.rigs, self.frigate = rigs, frigate
        self._coverage: dict[str, tuple[float, dict]] = {}  # camera -> (keyframes mtime, map)
        self._building: set[str] = set()

    def coverage(self, rig: Rig) -> dict:
        """The coverage map, rebuilt in the background after a save (about a second).

        The old map is served until the new one is ready.
        """
        name, mtime = rig.cfg.name, rig.keyframes.mtime()
        if name not in self._coverage:
            self._coverage[name] = mtime, self._build(rig)
        elif self._coverage[name][0] != mtime and name not in self._building:
            self._building.add(name)

            def later() -> None:
                try:
                    self._coverage[name] = mtime, self._build(rig)
                finally:
                    self._building.discard(name)
            threading.Thread(target=later, daemon=True).start()
        return self._coverage[name][1]

    @staticmethod
    def _build(rig: Rig) -> dict:
        return geometry.coverage_map(rig.model, rig.keyframes.load(), rig.cfg.pan_range)


def nearby(model: ZoneModel, pan: int, tilt: int, kfs: list[Keyframe]) -> dict | None:
    """The keyframe the camera sits on, with its own zone moved to the current view, or None."""
    k = min(kfs, key=lambda k: abs(k.pan - pan) + abs(k.tilt - tilt), default=None)
    if k is None or abs(k.pan - pan) > NEAR or abs(k.tilt - tilt) > NEAR:
        return None
    alone = replace(model, agree=1, max_dist=float("inf"))
    # guess: what the other keyframes predict here, to see where this one disagrees with them
    return {"pan": k.pan, "tilt": k.tilt, "zone": geometry.predict(alone, pan, tilt, [k]),
            "guess": geometry.predict(model, pan, tilt, [x for x in kfs if x is not k])}


class Handler(BaseHTTPRequestHandler):
    server: Editor

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, body: Any, ctype: str = "application/json", code: int = 200) -> None:
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> tuple[Rig | None, str]:
        """(camera, action) for /api/<camera>/<action>, else (None, the path without /api/)."""
        parts = self.path.split("?")[0].strip("/").split("/")
        if parts[:1] == ["api"] and len(parts) >= 3 and parts[1] in self.server.rigs:
            return self.server.rigs[parts[1]], parts[2]
        return None, parts[0] if parts[:1] != ["api"] else "/".join(parts[1:])

    def _handle(self, run: Callable[[], Any]) -> None:
        try:
            res = run()
            if res is not None:
                self._send(res)
        except ApiError as e:
            self._send({"error": str(e)}, code=e.code)
        except Exception as e:
            log.exception("editor: %s %s", self.command, self.path.split("?")[0])
            self._send({"error": f"{type(e).__name__}: {e}"}, code=500)

    # --- GET ---------------------------------------------------------------------------------

    def do_GET(self) -> None:
        self._handle(self._get)

    def _get(self) -> Any:
        rig, what = self._route()
        if rig is None and what == "":
            return self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        if rig is None and what == "cameras":
            return list(self.server.rigs)
        if rig is not None and what == "frame.jpg":
            return self._send(self.server.frigate.latest_jpg(rig.cfg.name), "image/jpeg")
        if rig is not None and what == "state":
            return self._state(rig)
        raise ApiError(404, "not found")

    def _state(self, rig: Rig) -> dict:
        pan, tilt = rig.camera.position()
        kfs = rig.keyframes.load()
        zone = rig.follower.zone
        return {"pan": pan, "tilt": tilt, "moving": rig.motion.busy,
                "zone": geometry.predict(rig.model, pan, tilt, kfs) if zone is UNSET else zone,
                "keyframes": [{"pan": k.pan, "tilt": k.tilt} for k in kfs],
                "here": nearby(rig.model, pan, tilt, kfs), "near": NEAR,
                "paused": rig.motion.paused_for(), "undo": rig.keyframes.can_undo,
                "calibrated": rig.motion.learned.calibrated(rig.cfg.name),
                "coverage": self.server.coverage(rig)}

    # --- POST --------------------------------------------------------------------------------

    def do_POST(self) -> None:
        self._handle(self._post)

    def _post(self) -> Any:
        rig, what = self._route()
        action = POST.get(what)
        if rig is None or action is None:
            raise ApiError(404, "not found")
        # Only JSON: a browser sends that cross-site only after a preflight, which this server refuses
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            raise ApiError(415, "send application/json")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if rig.motion.paused:
            rig.motion.pause()  # any action keeps the pause going
        if what != "pause" and rig.motion.busy:
            raise ApiError(409, "camera is still moving")
        try:
            return action(rig, body) or {"ok": True}
        except Busy:
            raise ApiError(409, "camera is still moving") from None


def _pause(rig: Rig, body: dict) -> None:
    if body.get("on"):
        rig.motion.pause()
    else:
        rig.motion.resume()


def _move(rig: Rig, body: dict) -> None:
    op = body["op"]
    if op == "home":
        rig.camera.home()
    elif op == "Stop":
        rig.motion.release()
    elif op in OPS:
        rig.motion.hold(op, min(64, max(1, int(body.get("speed", 10)))))
    else:
        raise ApiError(400, "bad op")


def _calibrate(rig: Rig, body: dict) -> None:
    rig.motion.pause()  # the camera's own tracking would spoil the measurement
    rig.motion.start_calibration()  # eight short moves, then the pulse table for this camera


def _goto(rig: Rig, body: dict) -> dict:
    pan, tilt = int(body["pan"]), int(body["tilt"])
    rig.motion.start_goto(pan, tilt, tol=12)  # lands on a keyframe
    return {"ok": True, "pan": pan, "tilt": tilt}


def _centre(rig: Rig, body: dict) -> dict:
    """Bring the clicked point (0..1 on the frame) to the middle."""
    dp, dt = geometry.centre_offset(rig.model, float(body["x"]), float(body["y"]))
    pan, tilt = rig.camera.position()
    pan, tilt = round(pan + dp), round(tilt + dt)
    rig.motion.start_goto(pan, tilt, tol=25)  # a click is rough aim; it need not land as close as a keyframe
    return {"ok": True, "pan": pan, "tilt": tilt}


def _keyframe(rig: Rig, body: dict) -> None:
    pan, tilt = rig.camera.position()
    if abs(pan - body["pan"]) > 3 or abs(tilt - body["tilt"]) > 3:
        raise ApiError(409, "the camera moved while you edited; reset and draw again")
    coords = [min(1.0, max(0.0, float(v))) for v in body["coords"]]
    if len(coords) < 6 or len(coords) % 2:
        raise ApiError(400, "a zone needs at least 3 points")
    old = body.get("replace")  # the keyframe being edited, stored again at the current position
    rig.keyframes.save(pan, tilt, coords, body.get("label", "editor"),
                       replace=(old["pan"], old["tilt"]) if old else None)


def _delete(rig: Rig, body: dict) -> None:
    rig.keyframes.delete(body["pan"], body["tilt"])


def _optimize(rig: Rig, body: dict) -> dict:
    """Drop the redundant keyframes; the full set is kept for undo until the next edit."""
    kfs = rig.keyframes.load()
    kept = geometry.thin(rig.model, kfs)
    rig.keyframes.keep_only(kept)
    return {"ok": True, "kept": len(kept), "dropped": len(kfs) - len(kept)}


def _undo(rig: Rig, body: dict) -> None:
    if not rig.keyframes.undo():
        raise ApiError(409, "nothing to undo")


POST: dict[str, Callable[[Rig, dict], Any]] = {
    "pause": _pause, "move": _move, "calibrate": _calibrate, "goto": _goto, "centre": _centre,
    "keyframe": _keyframe, "delete": _delete, "optimize": _optimize, "undo": _undo,
}
