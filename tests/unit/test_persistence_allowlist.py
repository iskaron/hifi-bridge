"""UNIT (temporary directories, simulated phone, fake LMS, fake RAUC): the bridge creates only allow-listed files in its persistent data
directory (/var/lib/hifi-bridge = /data/hifi-bridge on the image). The allow-list is `bridge_files` in os/storage.json (DECISIONS #121,
docs/PERSISTENCE.md section 3.1); a new persistent file must be added there - with the reason why it has to survive a reboot."""
import fnmatch
import json
import os

from fakes import FakeLms
from fakes_ota import FakeRaucClient

from btbridge import bootfiles, provisioning as P
from btbridge.ota.manager import UpdateManager
from btbridge.service import ApplianceService
from btkit.sim_a2dp import SimulatedA2dpSource

REPO = os.path.join(os.path.dirname(__file__), "..", "..")
PATTERNS = [p[0] for p in json.load(open(os.path.join(REPO, "os", "storage.json")))["bridge_files"]["patterns"]]
DOC = {"format": P.FORMAT, "created_by": "test", "setup_code": "ABCD-EFGH-JKMN", "device_name": "Wohnzimmer",
       "trust_key": P.encode_trust_key(bytes(range(32))), "country": "DE",
       "hotspot": {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}, "wifi": {"ssid": "Home", "psk": "wifi-secret-123"}}


def allowed(rel: str) -> bool:
    parts = rel.split("/")
    return any(len(parts) == len(pat.split("/")) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pat.split("/")))
               for pat in PATTERNS)


def every_path(root):
    for dp, dns, fns in os.walk(root):
        for n in dns + fns:
            yield os.path.relpath(os.path.join(dp, n), root)


def test_the_matcher_is_strict():
    assert allowed("settings.json") and allowed("certs/ca.key") and allowed("ota/snapshots/A/settings.json")
    assert not allowed("faults.jsonl") and not allowed("ca.key") and not allowed("ota/snapshots/A/x/y") and not allowed("logs/x.log")


def test_a_full_lifecycle_creates_only_allow_listed_files(tmp_path):
    data, boot, run = tmp_path / "data", tmp_path / "boot", tmp_path / "run"
    boot.mkdir()
    run.mkdir()
    (boot / P.FILE_NAME).write_bytes(P.render(DOC))
    bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)          # provisioning, CA, setup state, status file
    lms = FakeLms()
    lms.add("00:04:20:00:00:01")
    sim = SimulatedA2dpSource()
    svc = ApplianceService(str(data), source=sim, stream_port=0, advertise_host="bridge", lms_factory=lambda url: lms, echo=None,
                           auth_iterations=1000, run_dir=str(run), discover=lambda timeout: [])
    svc.start()
    try:
        svc.store.update({"device_name": "Küche"})
        svc.logs.fault("thermal", "warning: 75 C")
        svc.journal.record("00:04:20:00:00:01", "http://bridge:8090/s/t/1.flac")
        rauc = FakeRaucClient()
        rauc.polls_until_done = 10 ** 9                                               # stays "installing": the bundle is still in use
        m = UpdateManager(str(data), rauc, trust={"mode": "own", "certificates": [], "official_source": None}, version="0.3.0",
                          log=lambda s: None, free=lambda p: 10 ** 12, reboot=lambda: None)
        path = m.begin_upload(12)
        open(path, "wb").write(b"bundle-bytes")
        rauc.bundles[m.bundle_path] = {"compatible": rauc.compatible, "version": "0.4.0"}
        m.finish_upload("hifi-bridge.raucb")
        m.install()                                                                   # pre-update snapshot + state
        files = sorted(every_path(data))
    finally:
        svc.stop()
    assert "settings.json" in files and "ota/bundle.raucb" in files and any(f.startswith("ota/snapshots/") for f in files)
    assert [f for f in files if not allowed(f)] == []
    assert not (data / "faults.jsonl").exists() and (run / "faults.jsonl").exists()
