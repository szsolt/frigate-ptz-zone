import numpy as np

from frigate_ptz_zone.fit_shift import picture_shift, solve


def test_solve_recovers_the_shift():
    m = np.array([[-0.9, 0.01], [0.02, -0.75]])
    turns = [(60, 2), (-58, -1), (3, 70), (-2, -69)]
    assert solve(turns, [tuple(m @ t) for t in turns]) == [-0.9, 0.01, 0.02, -0.75]


def test_picture_shift_sign():
    rng = np.random.default_rng(0)
    scene = np.uint8(rng.integers(0, 255, (300, 500, 3)))
    a, b = scene[50:250, 50:450], scene[50:250, 70:470]  # camera turned right: content moves 20 px left
    dx, dy, conf = picture_shift(a.copy(), b.copy())
    assert abs(dx + 20) < 0.5 and abs(dy) < 0.5 and conf > 0.5


def test_picture_shift_ignores_on_screen_text():
    rng = np.random.default_rng(1)
    scene = np.uint8(rng.integers(0, 255, (300, 500, 3)))
    a, b = scene[50:250, 50:450].copy(), scene[50:250, 70:470].copy()
    clock = np.uint8(rng.integers(0, 255, (15, 200, 3)))
    a[185:200, 100:300] = b[185:200, 100:300] = clock  # same text at the bottom of both
    dx, dy, _ = picture_shift(a, b)
    assert abs(dx + 20) < 0.5 and abs(dy) < 0.5
