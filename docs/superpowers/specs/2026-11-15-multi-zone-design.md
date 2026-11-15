# Several zones and masks per camera

## Why

A user asked for "object incoming" alerts: a person who enters `street` and then `yard`, in that order.
On a PTZ camera both zones must turn with the camera. Today only one zone per camera follows.
The order rule itself stays in HA or Node-RED, which read the zone order from Frigate's events.

## Goals

- Any number of a camera's Frigate zones can follow the camera.
- Any number of its motion masks can be "sky" masks, on while the camera looks up past a tilt.
  Today that is the single `mask` + `mask_tilt` (e.g. a treetops mask), and the mask can have any name.
- Each zone and mask gets its role in the editor: **follows**, **sky** or **not managed**.
- Existing setups keep working with no change to cameras.json or the keyframe file.

## Non-goals

- No "incoming" or direction logic in ptz-zone.
- No creating zones or masks in our editor. They are made in Frigate's UI, where their objects, inertia
  and alert settings live. Our editor lists what Frigate has for the camera.
- Masks that follow the camera with keyframes (my call: not now; only zones follow).
- A "sky" role for zones (my call: sky is for motion masks only).

## Anti-goals

- A managed zone that sweeps over a parked car must not look like a person walking in.
  Before building, check how Frigate records `entered_zones` when a zone moves or is switched off and on.
  Then test street → yard live on a real camera.
- Two zones that update at different times. All of a camera's zones are written in one Frigate call.

## Approaches

- **A: one keyframe set per zone (picked).** The existing per-zone math runs once per zone.
  Each zone has its own coverage and positions. Very little new geometry code.
- **B: one keyframe holds every zone.** Saves clicks, but needs a "not in view" state per zone,
  and thinning and coverage would need reworking. Dropped.
- Storage: the user picked **one keyframe file per camera**, not one file per zone.

## Design

- **Keyframes:** each keyframe gets a `zone` field. A keyframe without it belongs to the camera's `zone`
  from cameras.json, so existing files stay valid. Save, delete and Optimize work on one zone.
  Undo restores the whole file, as today.
- **cameras.json:**
  - `"zones": {"street": "follow", "yard": "follow"}`
  - `"masks": {"treetops": {"sky": 123}}`
  - The old `zone`, `mask` and `mask_tilt` are still read and mean the same as before.
  - These keys must not fall through to the adapter options.
- **Editor:**
  - A roles panel lists the camera's Frigate zones and motion masks, read fresh when it opens.
  - Each gets a role: follows / sky (with its tilt; the editor shows the current tilt) / not managed.
  - Saving writes cameras.json and takes effect at once, with no restart.
  - A zone picker chooses which zone you draw. The other followed zones show faintly.
  - The coverage map is for the picked zone.
- **Follower:**
  - Works out each followed zone from its own keyframes.
  - Writes all changed zones in one Frigate call.
  - Position lost: all followed zones and sky masks switch off together, as today.
  - A zone set to "not managed" is never touched again.

## Left to the builder

- How the editor's write of cameras.json keeps hand edits and key order intact.
- The exact shape of the roles panel, within the editor's current style.
- What a followed zone with no keyframes yet does (suggested: switched off, as today with no keyframes).
