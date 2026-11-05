"""The few Frigate HTTP API calls the service makes."""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request


class Frigate:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self._write = threading.Lock()  # one config write at a time

    def set_zone(self, camera: str, zone: str, coords: str | None) -> None:
        """Write the zone's coordinates, or switch it off when coords is None."""
        z = f"cameras.{camera}.zones.{zone}"
        q = {f"{z}.enabled": "false"} if coords is None else {f"{z}.coordinates": coords, f"{z}.enabled": "true"}
        self._config_set(camera, q, "zones")

    def set_mask(self, camera: str, mask: str, on: bool) -> None:
        """Switch a motion mask on or off. Its shape is drawn in Frigate's UI."""
        self._config_set(camera, {f"cameras.{camera}.motion.mask.{mask}.enabled": str(on).lower()}, "motion")

    def zone_coords(self, camera: str, zone: str) -> list[float]:
        """The zone as Frigate's config has it now."""
        with urllib.request.urlopen(f"{self.url}/api/config", timeout=10) as r:
            coords = json.loads(r.read())["cameras"][camera]["zones"][zone]["coordinates"]
        return [float(v) for v in coords.split(",")]

    def latest_jpg(self, camera: str, height: int = 540) -> bytes:
        with urllib.request.urlopen(f"{self.url}/api/{camera}/latest.jpg?height={height}", timeout=10) as r:
            return r.read()

    def _config_set(self, camera: str, query: dict[str, str], topic: str) -> None:
        """Frigate saves config.yml and applies the change live."""
        req = urllib.request.Request(
            f"{self.url}/api/config/set?{urllib.parse.urlencode(query, safe=',')}", method="PUT",
            data=json.dumps({"requires_restart": 0, "update_topic": f"config/cameras/{camera}/{topic}"}).encode(),
            headers={"Content-Type": "application/json"})
        with self._write, urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read())
        if not res.get("success"):
            raise RuntimeError(res)
