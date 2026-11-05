from collections import deque

import pytest
from fakes import FakeCamera, FakeFrigate

from frigate_ptz_zone import follower as follower_module
from frigate_ptz_zone.config import CameraConfig
from frigate_ptz_zone.follower import Follower, frame_positions, sky_up
from frigate_ptz_zone.keyframes import KeyframeStore


def test_still_camera_is_one_position():
    seen = deque([(0, (1, 1)), (1, (1, 1)), (2, (1, 1))])
    assert frame_positions(seen, 10, lag=(1, 4.5)) == ((1, 1),)


def test_moving_camera_covers_the_lag_window():
    seen = deque([(0, (0, 0)), (5, (1, 0)), (6, (2, 0)), (8, (3, 0)), (9.5, (4, 0))])
    # the reading at 5 s is still in effect at the window start (5.5 s); the one at 0 s is dropped
    assert frame_positions(seen, 10, lag=(1, 4.5)) == ((1, 0), (2, 0), (3, 0))
    assert seen[0] == (5, (1, 0))


def test_first_reading_counts_before_the_lag():
    assert frame_positions(deque([(9.8, (5, 5))]), 10, lag=(1, 4.5)) == ((5, 5),)
    assert frame_positions(deque(), 10) == ()


def test_sky_up():
    assert sky_up(123, (1800, 0)) and sky_up(123, (1240, 120))
    assert not sky_up(123, (1238, 147)) and not sky_up(123, None)


@pytest.fixture
def rig(tmp_path):
    cfg = CameraConfig.from_dict("cam", {"zone": "z", "grid": [160, 90], "shift": [-0.5, 0, 0, 0.4],
                                         "mask": "sky", "mask_tilt": 100, "pan_min": 0, "pan_max": 1000}, tmp_path)
    store = KeyframeStore(cfg.keyframes)
    store.save(500, 500, [0, 0, 1, 0, 1, 1], "a")
    camera, frigate = FakeCamera((500, 500)), FakeFrigate()
    return Follower(cfg, cfg.model, camera, store, frigate), camera, frigate


def test_zone_follows_and_is_not_rewritten(rig):
    f, _camera, frigate = rig
    f.step()
    f._last_push = 0  # past MIN_PUSH
    f.step()
    assert len(frigate.zones) == 1 and frigate.zones[0] and frigate.masks == [False]


def test_zone_off_when_position_lost(rig, monkeypatch):
    f, camera, frigate = rig
    f.step()
    camera.pos = OSError("down")
    f.step()
    assert frigate.zones[-1] is not None  # a short gap changes nothing
    monkeypatch.setattr(follower_module, "LOST_AFTER", -1)
    f.step()
    f.step()
    assert frigate.zones[1:] == [None] and frigate.masks == [False]


def test_out_of_range_sends_home_and_once_more_on_return(rig):
    f, camera, _ = rig
    camera.pos = (2000, 500)
    f.step()
    f.step()
    camera.pos = (900, 500)
    f.step()
    f.step()
    assert camera.moves == ["home", "home", "home"]
