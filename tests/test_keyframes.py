import json

from frigate_ptz_zone.keyframes import Keyframe, KeyframeStore

ZONE = [0, 0, 1, 0, 1, 1]


def store(tmp_path):
    path = tmp_path / "keyframes.json"
    path.write_text("[]")
    return KeyframeStore(path)


def test_save_replaces_same_position_and_sorts(tmp_path):
    s = store(tmp_path)
    assert s.save("z", 10, 5, ZONE, "a") == 1
    assert s.save("z", 0, 5, ZONE, "b") == 2
    assert s.save("z", 10, 5, ZONE, "c") == 2
    assert [(k.pan, k.label) for k in s.load()] == [(0, "b"), (10, "c")]
    assert json.loads(s.path.read_text())[0]["points"] == 3


def test_save_with_replace_moves_the_keyframe(tmp_path):
    s = store(tmp_path)
    s.save("z", 10, 5, ZONE, "a")
    s.save("z", 12, 6, ZONE, "a", replace=(10, 5))
    assert [(k.pan, k.tilt) for k in s.load()] == [(12, 6)]


def test_delete(tmp_path):
    s = store(tmp_path)
    s.save("z", 10, 5, ZONE, "a")
    s.delete("z", 10, 5)
    assert s.load() == []


def test_keep_only_and_undo(tmp_path):
    s = store(tmp_path)
    s.save("z", 1, 1, ZONE, "a")
    s.save("z", 2, 2, ZONE, "b")
    assert not s.can_undo
    s.keep_only("z", [s.load()[0]])
    assert len(s.load()) == 1 and s.can_undo
    assert s.undo() and len(s.load()) == 2 and not s.can_undo
    assert not s.undo()


def test_an_edit_after_optimize_ends_the_undo(tmp_path):
    s = store(tmp_path)
    s.save("z", 1, 1, ZONE, "a")
    s.keep_only("z", [])
    s.save("z", 2, 2, ZONE, "b")
    assert not s.can_undo and len(s.load()) == 1


def test_round_trip():
    k = Keyframe(1, 2, (0.5, 0.5), "x", "2026-11-05T00:00:00", "z")
    assert Keyframe.from_dict(k.to_dict()) == k


def test_missing_file_is_empty(tmp_path):
    s = KeyframeStore(tmp_path / "none.json")
    assert s.load() == [] and s.mtime() == 0.0
    s.save("z", 1, 1, ZONE, "first")
    assert len(s.load()) == 1


def test_one_zone_edits_leave_the_others_and_old_keyframes_are_the_old_zone(tmp_path):
    path = tmp_path / "k.json"
    old = Keyframe(1, 1, tuple(ZONE), "old", "t").to_dict()
    del old["zone"]  # written before there were several zones
    path.write_text(json.dumps([old]))
    s = KeyframeStore(path, "yard")
    s.save("street", 1, 1, ZONE, "new")  # same position, other zone: both stay
    s.save("street", 2, 2, ZONE, "new")
    s.delete("street", 2, 2)
    s.keep_only("street", [])
    assert [(k.zone, k.label) for k in s.load()] == [("yard", "old")]
    s.save("street", 3, 3, ZONE, "new")
    s.delete("yard", 1, 1)
    assert [(k.zone, k.pan) for k in s.load()] == [("street", 3)]
