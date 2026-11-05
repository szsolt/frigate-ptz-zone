# Contributing

Issues and pull requests are welcome. I try every change on my own Reolink cameras first, so a change to how the camera moves can take me a few days to test.

## Run the tests

```sh
pip install -e ".[fit,test]"
ruff check .
pytest
```

The tests use a fake camera and a fake Frigate, so you don't need any hardware.

## Add support for another camera

Everything that talks to a camera lives in `frigate_ptz_zone/adapters/`, one file per make. To add one:

1. Copy `adapters/reolink.py` to `adapters/<make>.py`.
2. Rewrite its `Camera` class for your camera. It needs these methods:
   - `position()`: where the camera points now, as pan and tilt in the camera's own units. Tilt must grow as the camera looks down.
   - `move(op, speed)`: start turning one way, for example `"Left"`, or stop with `"Stop"`.
   - `home()`: go back to the home view.
   - `suspend()` and `restore(state)`: switch the camera's own tracking off, and back on as it was. Optional.
   - `close()`: log out when the service stops. Optional.

   Wrap every request to the camera in `with self.lock:`. That way two requests never overlap, and nothing gets sent between a timed move and its stop.
3. In `cameras.json`, set `"type": "<make>"` for that camera. Its other keys, like `host`, reach your class as `self.options`.
4. Run `fit_shift` on the camera. Check that each move it prints turned the camera and moved the picture.

The camera has to be able to report where it points. Without that, the zone can't follow.

## Code style

- Python 3.12 or newer, and the standard library where it does the job. A new dependency needs a good reason.
- Short functions with type hints. Comments say why, not what.
- Small pull requests: one change each, with a test where it makes sense.
