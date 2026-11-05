import json
import threading
import urllib.error
import urllib.request

import pytest
from fakes import FakeCamera

from frigate_ptz_zone.config import CameraConfig
from frigate_ptz_zone.editor import Editor
from frigate_ptz_zone.follower import Follower
from frigate_ptz_zone.keyframes import KeyframeStore
from frigate_ptz_zone.motion import Learned, Motion, Pauses
from frigate_ptz_zone.service import Rig


@pytest.fixture
def url(tmp_path):
    (tmp_path / "k.json").write_text("[]")
    cfg = CameraConfig.from_dict("cam", {"zone": "z", "keyframes": "k.json", "grid": [160, 90],
                                         "shift": [-0.5, 0, 0, 0.4]}, tmp_path)
    camera, store = FakeCamera(), KeyframeStore(cfg.keyframes)
    rig = Rig(cfg, cfg.model, camera, store, Follower(cfg, cfg.model, camera, store, None),
              Motion("cam", camera, Learned(tmp_path / "l.json"), Pauses(tmp_path / "p.json")))
    server = Editor(0, {"cam": rig}, None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def call(url, path, body=None, ctype="application/json"):
    req = urllib.request.Request(url + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_get(url):
    assert call(url, "/api/cameras") == (200, ["cam"])
    assert call(url, "/api/nope/state")[0] == 404
    code, state = call(url, "/api/cam/state")
    assert code == 200 and state["zone"] is None and state["keyframes"] == [] and not state["moving"]


def test_post_errors(url):
    assert call(url, "/api/cam/move", {"op": "Spin"}) == (400, {"error": "bad op"})
    assert call(url, "/api/cam/undo", {}) == (409, {"error": "nothing to undo"})
    assert call(url, "/api/cam/keyframe", {"pan": 0, "tilt": 0, "coords": [0, 0, 1, 1]})[0] == 400
    assert call(url, "/api/cam/keyframe", {"pan": 50, "tilt": 0, "coords": [0, 0, 1, 0, 1, 1]})[0] == 409
    assert call(url, "/api/cam/nope", {})[0] == 404


def test_post_needs_json(url):
    # a cross-site form or text/plain POST needs no preflight, so it must be refused
    assert call(url, "/api/cam/pause", {"on": 1}, ctype="text/plain")[0] == 415


def test_keyframe_optimize_undo(url):
    zone = [0, 0, 1, 0, 1, 1]
    assert call(url, "/api/cam/keyframe", {"pan": 0, "tilt": 0, "coords": zone}) == (200, {"ok": True})
    _code, state = call(url, "/api/cam/state")
    assert state["keyframes"] == [{"pan": 0, "tilt": 0}] and state["here"]["pan"] == 0
    assert call(url, "/api/cam/optimize", {}) == (200, {"ok": True, "kept": 1, "dropped": 0})
    assert call(url, "/api/cam/undo", {}) == (200, {"ok": True})
    assert call(url, "/api/cam/delete", {"pan": 0, "tilt": 0}) == (200, {"ok": True})
    assert call(url, "/api/cam/state")[1]["keyframes"] == []
