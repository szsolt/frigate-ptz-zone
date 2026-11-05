import pytest

from frigate_ptz_zone.adapters import PTZ, open_camera, reolink


def test_reolink_opens_without_contacting_the_camera():
    assert isinstance(open_camera("reolink", {"host": "h"}), PTZ)


@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setenv("REOLINK_USER", "u")
    monkeypatch.setenv("REOLINK_PASSWORD", "p")
    cam = reolink.Camera({"host": "h"})
    cam.calls = []
    return cam


def answer(camera, monkeypatch, handler):
    def call(cmd, param, token=True):
        camera.calls.append(cmd)
        return handler(cmd, param)
    monkeypatch.setattr(camera, "_call", call)


def test_relogin_on_expired_token(camera, monkeypatch):
    expired = iter([True])

    def handler(cmd, param):
        if cmd == "Login":
            return {"Token": {"name": "t"}}
        if next(expired, False):
            raise RuntimeError("GetPtzCurPos: please login first")
        return {"PtzCurPos": {"Ppos": 1, "Tpos": 2}}
    answer(camera, monkeypatch, handler)
    assert camera.position() == (1, 2)
    assert camera.calls == ["Login", "GetPtzCurPos", "Login", "GetPtzCurPos"]


def test_refused_login_backs_off_but_no_answer_retries_soon(camera, monkeypatch):
    def refused(cmd, param):
        raise RuntimeError("Login: invalid user")
    answer(camera, monkeypatch, refused)
    with pytest.raises(RuntimeError):
        camera.login()
    assert camera.backoff == 60

    camera.backoff = camera.retry_at = 0

    def offline(cmd, param):
        raise OSError("timed out")
    answer(camera, monkeypatch, offline)
    with pytest.raises(OSError):
        camera.login()
    assert camera.backoff == 0 and camera.retry_at - reolink.time.monotonic() <= 10


def test_failed_suspend_puts_tracking_back(camera, monkeypatch):
    sent = []

    def handler(cmd, param):
        if cmd == "GetAiCfg":
            return {"bSmartTrack": 1, "aiTrack": 1}
        if cmd == "GetPtzGuard":
            return {"PtzGuard": {"benable": 1}}
        sent.append((cmd, param))
        if cmd == "SetPtzGuard" and param["PtzGuard"]["benable"] == 0:
            raise RuntimeError("busy")
    camera.token = "t"
    answer(camera, monkeypatch, handler)
    with pytest.raises(RuntimeError):
        camera.suspend()
    assert sent[-2][1]["bSmartTrack"] == 1 and sent[-1][1]["PtzGuard"]["benable"] == 1


def test_suspend_without_auto_tracking(camera, monkeypatch):
    def handler(cmd, param):
        if cmd == "GetAiCfg":
            raise RuntimeError("GetAiCfg: not support")
        if cmd == "GetPtzGuard":
            return {"PtzGuard": {"benable": 1}}
    camera.token = "t"
    answer(camera, monkeypatch, handler)
    assert camera.suspend() == {"track": None, "guard": 1}
    assert "SetAiCfg" not in camera.calls
