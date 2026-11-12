"""Reolink PTZ cameras over their HTTPS API. Login from REOLINK_USER and REOLINK_PASSWORD, one for all cameras.

cameras.json: "host" is the camera's address. "position" is how the position is read: "baichuan"
(default; Reolink's own protocol on port 9000, which the Home Assistant integration uses too) or "https".
Some models, such as the TrackMix, leave the tilt out of the HTTPS reply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import ssl
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from reolink_aio.api import Host
from reolink_aio.exceptions import CredentialsInvalidError

from . import PTZ

log = logging.getLogger(__name__)
logging.getLogger("reolink_aio").setLevel(logging.ERROR)  # it warns about every camera event it doesn't know

NO_VERIFY = ssl.create_default_context()
NO_VERIFY.check_hostname = False
NO_VERIFY.verify_mode = ssl.CERT_NONE  # the camera has a self-signed certificate


class Camera(PTZ):
    def __init__(self, options: dict[str, Any]):
        super().__init__(options)
        self.host, self.token, self.backoff, self.retry_at = options["host"], None, 0, 0
        self.via = options.get("position", "baichuan")
        if self.via not in ("baichuan", "https"):
            raise ValueError(f'"position" must be "baichuan" or "https", not {self.via!r}')
        self._bc: Host | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _call(self, cmd: str, param: dict, token: bool = True) -> Any:
        q = {"cmd": cmd, "token": self.token} if token else {"cmd": cmd}
        req = urllib.request.Request(
            f"https://{self.host}/api.cgi?{urllib.parse.urlencode(q)}",
            data=json.dumps([{"cmd": cmd, "action": 0, "param": param}]).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5, context=NO_VERIFY) as r:
            res = json.loads(r.read())[0]
        if res.get("code") != 0:
            raise RuntimeError(f"{cmd}: {res.get('error', res)}")
        return res["value"]

    def login(self) -> None:
        # The camera locks logins for 5 min after 10 failures, so a refused login waits
        # instead of retrying (1, 2, 4 ... 30 min).
        if time.monotonic() < self.retry_at:
            raise RuntimeError("login failed earlier, waiting before the next try")
        try:
            v = self._call("Login", {"User": {"Version": "0", "userName": os.environ["REOLINK_USER"],
                                              "password": os.environ["REOLINK_PASSWORD"]}}, token=False)
        except OSError:  # no answer: not a failed login, and the camera may be back any moment
            self.retry_at = time.monotonic() + 10
            raise
        except Exception as e:
            if "max session" in str(e):  # sessions full, not a wrong login: try again in a minute
                self.retry_at = time.monotonic() + 60
                raise
            self._login_refused()
            raise
        self.token, self.backoff, self.retry_at = v["Token"]["name"], 0, 0

    def _login_refused(self) -> None:
        self.backoff = min(self.backoff * 2 or 60, 1800)
        self.retry_at = time.monotonic() + self.backoff

    def request(self, cmd: str, param: dict) -> Any:
        with self.lock:  # one login at a time; parallel logins would cancel each other's token
            if not self.token:
                self.login()
            try:
                return self._call(cmd, param)
            except RuntimeError as e:
                if "login" not in str(e).lower() and "-6" not in str(e):  # only an expired token ("please login first", -6) needs a new one
                    raise
                self.token = None
                self.login()
                return self._call(cmd, param)

    def position(self) -> tuple[int, int]:
        if self.via == "https":
            v = self.request("GetPtzCurPos", {"PtzCurPos": {"channel": 0}})
            return int(v["PtzCurPos"]["Ppos"]), int(v["PtzCurPos"]["Tpos"])
        with self.lock:
            if time.monotonic() < self.retry_at:  # the same wait as login(): both count towards the camera's lockout
                raise RuntimeError("login failed earlier, waiting before the next try")
            try:
                xml = self._run(self._bc_send(433))  # GetPtzCurPos
            except CredentialsInvalidError:
                self._login_refused()
                raise
        root = ET.fromstring(xml)
        return int(root.findtext(".//pPos")), int(root.findtext(".//tPos"))

    async def _bc_send(self, cmd_id: int) -> str:
        if self._bc is None:  # made on the loop it runs on
            self._bc = Host(self.host, os.environ["REOLINK_USER"], os.environ["REOLINK_PASSWORD"])
        return await self._bc.baichuan.send(cmd_id=cmd_id, channel=0)

    def _run(self, coro: Any) -> Any:
        """Run a reolink_aio coroutine on this camera's own event loop, from the caller's thread."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True).start()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=5)
        except TimeoutError:
            fut.cancel()
            raise

    def move(self, op: str, speed: int = 10) -> None:
        self.request("PtzCtrl", {"channel": 0, "op": op, "speed": speed})

    def home(self) -> None:
        """The guard position, the same place guard return goes."""
        self.request("SetPtzGuard", {"PtzGuard": {"channel": 0, "cmdStr": "toPos", "bSaveCurrentPos": 0}})

    def suspend(self) -> dict:
        """Tracking and guard return off. Only benable changes, so the saved guard view stays."""
        try:
            ai = self.request("GetAiCfg", {"channel": 0})
            track = [ai["bSmartTrack"], ai["aiTrack"]]
        except (RuntimeError, KeyError):
            track = None  # a model without auto tracking
        state = {"track": track, "guard": self.request("GetPtzGuard", {"channel": 0})["PtzGuard"]["benable"]}
        try:
            self.restore({"track": None if track is None else [0, 0], "guard": 0})
        except Exception:
            self.restore(state)  # half switched off: put back what was read, or it is lost
            raise
        return state

    def restore(self, state: dict) -> None:
        if state["track"] is not None:
            # bSmartTrack 0 alone still let the camera follow a car, so aiTrack goes too
            self.request("SetAiCfg", {"channel": 0, "bSmartTrack": state["track"][0], "aiTrack": state["track"][1]})
        self.request("SetPtzGuard", {"PtzGuard": {"channel": 0, "cmdStr": "setPos", "benable": state["guard"]}})

    def close(self) -> None:
        """Free the session; the camera only has a few, and a dropped one stays taken for an hour."""
        with self.lock:
            if self.token:
                try:
                    self._call("Logout", {})
                except Exception as e:  # noqa: BLE001 - an adapter can raise anything
                    log.info("%s: logout failed: %s", self.host, type(e).__name__)
                self.token = None
            if self._bc is not None:
                try:
                    self._run(self._bc.logout())  # also closes its HTTP session; no HTTP token, so nothing is sent
                except Exception as e:  # noqa: BLE001
                    log.info("%s: Baichuan logout failed: %s", self.host, type(e).__name__)
                self._bc = None
