"""A camera and a Frigate that record what is sent to them."""
import threading


class FakeCamera:
    def __init__(self, pos=(0, 0)):
        self.lock, self.moves, self.restored, self.suspends = threading.RLock(), [], None, 0
        self.pos = pos  # an Exception instance makes position() raise it

    def position(self):
        if isinstance(self.pos, Exception):
            raise self.pos
        return self.pos

    def move(self, op, speed=10):
        self.moves.append(op)

    def home(self):
        self.moves.append("home")

    def suspend(self):
        self.suspends += 1
        return {"track": [1, 3]}

    def restore(self, state):
        self.restored = state


class FakeFrigate:
    def __init__(self):
        self.zones, self.masks = [], []

    def set_zone(self, camera, zone, coords):
        self.zones.append(coords)

    def set_mask(self, camera, mask, on):
        self.masks.append(on)
