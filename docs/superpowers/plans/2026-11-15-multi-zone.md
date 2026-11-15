# Several zones and masks per camera: plan

Spec: `docs/superpowers/specs/2026-11-15-multi-zone-design.md`. Tests only for the tricky parts.

## Decisions

- cameras.json: `"zones": ["street", "yard"]` lists the followed zones. `"masks": {"treetops": 123}` lists sky masks with their tilt.
- Old keys still load: `zone` becomes `zones: [zone]`; `mask` + `mask_tilt` become `masks`.
- A keyframe without a `zone` field belongs to the old `zone`. Every write tags all keyframes, so the file migrates by itself.
- The editor's role save writes `zones` and `masks`, drops the old keys, and tags the keyframe file first.
- Frigate: one config call sets every changed zone of a camera.

## Steps

1. **Config:** parse `zones` and `masks` plus the old keys. Add a writer for one camera's roles that keeps every other key.
2. **Keyframes:** add a `zone` field. Save, delete and keep-only act on one zone and leave the others alone. Undo stays whole-file.
3. **Frigate:** set several zones in one call. List a camera's zone and motion-mask names.
4. **Follower:** track each followed zone, push all changes in one call, and handle several sky masks. Lost position switches all of them off. Roles can change while it runs, and a dropped zone is never touched again.
5. **Editor API:** state, coverage, keyframe, delete and optimize take a zone. Add roles GET (Frigate names + current roles) and roles POST (writes cameras.json, applies at once).
6. **Editor page:** a zone picker, the other followed zones drawn faintly, and a roles panel. Check it in headless Chrome.
7. **CLI and fit_lens:** `add` takes the zone. fit_lens fits each zone's keyframes on its own.
8. **README:** the new keys and the roles panel. Note that Frigate counts a standing person who is swept by a moving zone, so an "incoming" rule should ignore stationary objects.
9. **Verify:** lint, tests, deploy, add a second zone on a real camera, and test street → yard live.

## Tricky tests

- Saving, deleting or optimizing one zone's keyframes leaves the other zone's keyframes untouched, and old untagged keyframes count as the old zone.
- The roles writer keeps adapter keys and drops the old `zone`/`mask`/`mask_tilt` keys.
- The follower writes two zones in one call, switches both off when the position is lost, and stops touching a zone after it is dropped from the roles.
