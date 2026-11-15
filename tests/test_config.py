import json
from pathlib import Path

import pytest

from frigate_ptz_zone.config import (
    CameraConfig,
    Lens,
    Settings,
    load_cameras,
    save_roles,
)


def test_flat_entry_maps_to_config():
    cfg = CameraConfig.from_dict("cam", {
        "host": "h", "zone": "z", "keyframes": "k.json", "grid": [160, 90], "shift": [1, 0, 0, 1],
        "lens": {"f": 1, "pan": 2, "tilt": 3, "k1": 0}, "max_dist": 200, "agree": 3,
        "mask": "sky", "mask_tilt": 100, "pan_min": 10, "pan_max": 20}, Path("/data"))
    assert cfg.adapter == "reolink" and cfg.options == {"host": "h"}
    assert cfg.keyframes == Path("/data/k.json") and cfg.pan_range == (10, 20)
    assert cfg.model.lens == Lens(1, 2, 3, 0) and cfg.model.max_dist == 200 and cfg.model.agree == 3
    assert cfg.zones == ("z",) and cfg.masks == {"sky": 100} and cfg.default_zone == "z"


def test_minimal_entry_gets_defaults():
    cfg = CameraConfig.from_dict("cam", {"type": "onvif", "zone": "z", "keyframes": "k.json",
                                         "grid": [160, 90], "shift": [1, 0, 0, 1]}, Path("."))
    assert cfg.adapter == "onvif" and cfg.options == {} and cfg.pan_range is None and cfg.masks == {}
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


def test_save_roles_keeps_other_keys_and_drops_the_old_ones(tmp_path):
    s = Settings.from_env({"DATA_DIR": str(tmp_path)})
    s.cameras_file.write_text(json.dumps({"cam": {"host": "h", "zone": "z", "mask": "sky", "mask_tilt": 100},
                                          "other": {"host": "o", "zone": "y"}}))
    save_roles(s, "cam", ["street", "z"], {"treetops": 120})
    data = json.loads(s.cameras_file.read_text())
    assert data["cam"] == {"host": "h", "zones": ["street", "z"], "masks": {"treetops": 120}}
    assert data["other"] == {"host": "o", "zone": "y"}
    cfg = load_cameras(s)["cam"]
    assert cfg.options == {"host": "h"} and cfg.zones == ("street", "z") and cfg.masks == {"treetops": 120}
