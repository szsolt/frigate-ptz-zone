"""Keep a Frigate zone on the ground while a PTZ camera pans and tilts.

One follower per camera reads pan/tilt about twice a second. For each new position it moves the zones
drawn at the nearest keyframes to the current view and keeps only the area where they agree; far
from every keyframe the zone is switched off. A camera can also switch a motion mask on while it
looks up (sky and treetops). The zone editor page records keyframes.

Cameras talk through adapters (adapters/), Reolink first.
"""

__version__ = "0.1.0"
