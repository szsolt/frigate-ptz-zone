from pathlib import Path

import pytest

from frigate_ptz_zone.config import CameraConfig, Lens, Settings


def test_flat_entry_maps_to_config():
    cfg = CameraConfig.from_dict("cam", {
        "host": "h", "zone": "z", "keyframes": "k.json", "grid": [160, 90], "shift": [1, 0, 0, 1],
        "lens": {"f": 1, "pan": 2, "tilt": 3, "k1": 0}, "max_dist": 200, "agree": 3,
        "mask": "sky", "mask_tilt": 100, "pan_min": 10, "pan_max": 20}, Path("/data"))
    assert cfg.adapter == "reolink" and cfg.options == {"host": "h"}
    assert cfg.keyframes == Path("/data/k.json") and cfg.pan_range == (10, 20)
    assert cfg.model.lens == Lens(1, 2, 3, 0) and cfg.model.max_dist == 200 and cfg.model.agree == 3
    assert (cfg.mask, cfg.mask_tilt) == ("sky", 100)


def test_minimal_entry_gets_defaults():
    cfg = CameraConfig.from_dict("cam", {"type": "onvif", "zone": "z", "keyframes": "k.json",
                                         "grid": [160, 90], "shift": [1, 0, 0, 1]}, Path("."))
    assert cfg.adapter == "onvif" and cfg.options == {} and cfg.pan_range is None and cfg.mask is None
    assert cfg.model.lens is None and cfg.model.agree == 2


def test_settings_from_env():
    s = Settings.from_env({"DATA_DIR": "/data"})
    assert s.cameras_file == Path("/data/cameras.json") and s.editor_port == 8781


def test_new_camera_without_shift_or_keyframes():
    cfg = CameraConfig.from_dict("cam", {"host": "h", "zone": "z", "max_dist": 100}, Path("/data"))
    assert cfg.model is None and cfg.options == {"host": "h"}
    assert cfg.keyframes == Path("/data/keyframes-cam.json")


def test_mask_needs_mask_tilt():
    with pytest.raises(ValueError):
        CameraConfig.from_dict("cam", {"zone": "z", "mask": "sky"}, Path("."))
