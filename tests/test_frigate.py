import json
import urllib.parse

from frigate_ptz_zone import frigate as frigate_module
from frigate_ptz_zone.frigate import Frigate


class Reply:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return json.dumps({"success": True}).encode()


def test_set_zone_query(monkeypatch):
    sent = []
    monkeypatch.setattr(frigate_module.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Reply())
    f = Frigate("http://frigate:5000/")
    f.set_zone("cam", "z", "0,0,1,0,1,1")
    f.set_zone("cam", "z", None)
    on, off = (dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.full_url).query)) for r in sent)
    assert on == {"cameras.cam.zones.z.coordinates": "0,0,1,0,1,1", "cameras.cam.zones.z.enabled": "true"}
    assert off == {"cameras.cam.zones.z.enabled": "false"}
    assert sent[0].full_url.startswith("http://frigate:5000/api/config/set?")
