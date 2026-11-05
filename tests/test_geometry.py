from dataclasses import replace

import numpy as np

from frigate_ptz_zone import geometry
from frigate_ptz_zone.config import Lens, ZoneModel
from frigate_ptz_zone.keyframes import Keyframe

MODEL = ZoneModel(grid=(160, 90), shift=(0, 0, 0, 0), agree=1, max_dist=1)
HALF = Keyframe(0, 0, (0, 0, 1, 0, 1, .5, 0, .5))
FULL = Keyframe(0, 0, (0, 0, 1, 0, 1, 1, 0, 1))
BOX = Keyframe(0, 0, (.3, .3, .7, .3, .7, .5, .3, .5))
LENS = replace(MODEL, lens=Lens(f=200, pan=0.1, tilt=0.1, k1=-0.05))


def test_predict_keeps_cut_corners():
    k = Keyframe(0, 0, (0, 0, 0, .35, .27, 1, 1, 1, 1, .27, .53, 0))
    assert len(geometry.predict_points(MODEL, 0, 0, [k])) == 6


def test_predict_off_far_from_keyframes():
    assert geometry.predict(replace(MODEL, shift=(1, 0, 0, 0)), 50, 0, [BOX]) is None


def test_agreement_is_overlap_over_union():
    assert geometry.agreement(MODEL, [HALF, HALF]) == [1.0, 1.0]
    assert 0.45 < geometry.agreement(MODEL, [HALF, FULL])[0] < 0.55


def test_warp_without_turn_or_distortion_changes_nothing():
    flat = replace(LENS, lens=replace(LENS.lens, k1=0))
    assert np.allclose(geometry.warp(flat, BOX, 0, 0), np.float32(BOX.coords).reshape(-1, 2) * [160, 90], atol=1e-3)


def test_warp_tilt():
    assert geometry.warp(LENS, BOX, 0, 10).max(axis=0)[1] < 45  # 1° down lifts the zone
    assert len(geometry.warp(LENS, BOX, 0, -400)) == 0           # 40° up loses it


def test_outermost_keyframes_are_not_scored():
    scores = geometry.agreement(MODEL, [replace(HALF, pan=-1), FULL, replace(HALF, pan=1)])
    assert scores[0] == scores[2] == 1.0 and 0.45 < scores[1] < 0.55


def test_thin_drops_the_copy_of_its_neighbours():
    kfs = [replace(HALF, pan=-1), HALF, FULL, replace(HALF, pan=1)]
    assert geometry.thin(MODEL, kfs) == [kfs[0], kfs[2], kfs[3]]


def test_predict_while_moving_keeps_only_the_common_zone():
    m = replace(MODEL, shift=(1, 0, 0, 0), max_dist=100)
    moving = geometry.predict_points(m, 0, 0, [BOX], more=[(20, 0)])
    assert moving[:, 0].min() > geometry.predict_points(m, 0, 0, [BOX])[:, 0].min()


def test_centre_offset_undoes_the_shift():
    m = replace(MODEL, shift=(-0.5, 0.1, 0.05, 0.4))
    dp, dt = geometry.centre_offset(m, 0.8, 0.3)
    k = Keyframe(0, 0, ())
    # turning by the offset moves the clicked point to the middle
    assert np.allclose(np.array([(0.8 - .5) * 160, (0.3 - .5) * 90]) + geometry.shift(m, k, dp, dt), 0)


def test_coverage_map():
    m = replace(MODEL, shift=(1, 0, 0, 1), max_dist=50)
    cov = geometry.coverage_map(m, [BOX], (-100, 100), steps=(4, 2))
    assert cov["box"] == [-100, 100, 0, 150] and cov["steps"] == [4, 2] and cov["fit"] == [1.0]
    assert [0, 0] in cov["off"] and [1, 0] not in cov["off"]
