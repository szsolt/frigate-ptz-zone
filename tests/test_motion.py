import json
import time

from fakes import FakeCamera

from frigate_ptz_zone.motion import FINE, MAX_PULSE, Learned, Motion, Pauses


def motion(tmp_path, camera=None):
    return Motion("cam", camera or FakeCamera(), Learned(tmp_path / "learned.json"), Pauses(tmp_path / "paused.json"))


def test_plan(tmp_path):
    m = motion(tmp_path)
    assert m.plan((0, 0), 5, 5, tol=12, fine=False) == ({}, FINE)
    assert m.plan((0, 0), 1000, 50, tol=12, fine=False) == ({"pan": 1000}, 20)
    assert m.plan((0, 0), 300, 0, tol=12, fine=False) == ({"pan": 300}, 10)
    assert m.plan((0, 0), 100, 30, tol=12, fine=False) == ({"pan": 100, "tilt": 30}, FINE)
    assert m.plan((0, 0), 300, 0, tol=12, fine=True) == ({"pan": 300}, FINE)


def test_learned_lead_skips_outliers_and_persists(tmp_path):
    learned = Learned(tmp_path / "learned.json")
    learned.learn_lead("cam", "pan", 10, 100)
    learned.learn_lead("cam", "pan", 10, 60)
    learned.learn_lead("cam", "pan", 10, 1000)
    learned.save()
    assert Learned(tmp_path / "learned.json").lead("cam", "pan", 10) == 80


def test_pause_and_resume(tmp_path):
    m = motion(tmp_path)
    m.pause()
    assert m.paused and 590 < m.paused_for() <= 600
    assert json.loads((tmp_path / "paused.json").read_text())["cam"]["restore"] == {"track": [1, 3]}
    m.pause()
    assert m.camera.suspends == 1
    m.resume()
    assert not m.paused and m.camera.restored == {"track": [1, 3]}


def test_pause_survives_a_restart_and_ends_on_time(tmp_path):
    m = motion(tmp_path)
    m.pause()
    m.tick()
    assert m.paused
    m.pauses.extend("cam", time.time() - 1)
    m2 = motion(tmp_path)
    m2.tick()
    assert not m2.paused and m2.camera.restored == {"track": [1, 3]}


def test_held_arrow_stops_without_keepalive(tmp_path):
    m = motion(tmp_path)
    m.hold("Left", 10)
    m.hold("Left", 10)
    assert m.camera.moves == ["Left"]
    m._held = ("Left", 10, 0)
    m.tick()
    assert m.camera.moves == ["Left", "Stop"]


def test_calibration_that_barely_moves_keeps_the_old_values(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    m = motion(tmp_path)
    m.calibrate()  # the fake camera never moves
    assert not m.learned.calibrated("cam")


def test_fine_pulse_is_capped(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    m = motion(tmp_path)
    m.learned.set_pulse("cam", "pan", 0, 0.5)
    m.drive((0, 0), {"pan": 300}, FINE)
    assert slept == [MAX_PULSE]
