"""UNIT (temporary directories, fake RAUC): the persistence rule at runtime (DECISIONS #121, docs/PERSISTENCE.md) - "persist only what must
survive a reboot". Diagnostics (the fault history) live in RAM; the boot step removes update staging files and the leftovers of atomic writes
that a power cut can leave behind; the boot partition's status file is only rewritten when its content changes."""
import json
import os

from fakes_ota import FakeRaucClient

from btbridge import appliance as AP
from btbridge import bootfiles, recovery, storage
from btbridge.ota import manager as M
from btbridge.ota.manager import UpdateManager
from btbridge.service import LogBuffer

TRUST = {"mode": "own", "certificates": [], "official_source": None}


# ---------------------------------------------------------------- fault history: RAM only (no file on the data partition)
def test_fault_history_without_a_directory_stays_in_memory_and_is_bounded(tmp_path):
    lb = LogBuffer(None, faults_keep=3, echo=None)
    for i in range(5):
        lb.fault("thermal", f"warning {i}")
    assert [f["detail"] for f in lb.faults()] == ["warning 2", "warning 3", "warning 4"]
    assert list(tmp_path.iterdir()) == []


def test_fault_history_in_the_runtime_directory_survives_a_service_restart(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    LogBuffer(str(run), echo=None).fault("lms", "unreachable")
    assert [f["kind"] for f in LogBuffer(str(run), echo=None).faults()] == ["lms"]


def test_the_service_keeps_no_fault_file_on_the_data_partition(tmp_path):
    from btbridge.service import ApplianceService
    data, run = tmp_path / "data", tmp_path / "run"
    run.mkdir()
    svc = ApplianceService(str(data), source=None, stream_port=0, echo=None, auth_iterations=1000, run_dir=str(run))
    svc.logs.fault("thermal", "critical")
    assert not (data / "faults.jsonl").exists() and (run / "faults.jsonl").exists()
    assert svc.logs.faults()[-1]["kind"] == "thermal"
    svc2 = ApplianceService(str(tmp_path / "data2"), source=None, stream_port=0, echo=None, auth_iterations=1000)
    svc2.logs.fault("thermal", "critical")
    assert svc2.logs.faults()[-1]["kind"] == "thermal" and not (tmp_path / "data2" / "faults.jsonl").exists()


def test_the_appliance_takes_the_runtime_directory_from_the_command_line(tmp_path):
    a = AP.parser().parse_args(["--data", str(tmp_path), "--run-dir", "/run/hifi-bridge"])
    assert a.run_dir == "/run/hifi-bridge"
    assert AP.parser().parse_args(["--data", str(tmp_path)]).run_dir is None


# ---------------------------------------------------------------- update staging: nothing survives the end of an update flow
def ota(tmp_path):
    data = tmp_path / "data"
    (data / "ota").mkdir(parents=True)
    return data, data / "ota"


def put(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_boot_cleanup_removes_every_transient_update_file(tmp_path):
    data, o = ota(tmp_path)
    leftovers = [put(o / "bundle.raucb.upload"), put(o / "bundle.raucb.part"), put(o / "bundle.raucb.part.json"),
                 put(o / "bundle.raucb"), put(o / "state.json.tmp-4711"), put(o / ".health-probe"),
                 put(o / "snapshots" / ".A.tmp" / "settings.json"), put(o / "snapshots" / ".A.old" / "settings.json")]
    keep = [put(o / "snapshots" / "A" / "meta.json", "{}"), put(o / "slots.json", "{}"), put(o / "health.json", "{}")]
    removed = M.cleanup_staging(str(data))
    assert not any(p.exists() for p in leftovers), removed
    assert all(p.exists() for p in keep)
    assert not (o / "snapshots" / ".A.tmp").exists() and not (o / "snapshots" / ".A.old").exists()
    assert "ota/bundle.raucb.upload" in removed and "ota/bundle.raucb" in removed


def test_a_bundle_the_update_state_still_needs_is_kept(tmp_path):
    """staged (verified, waiting for the owner's install) and installing (RAUC reads it; after a power cut the owner can retry)."""
    for phase in ("staged", "installing"):
        data, o = ota(tmp_path / phase)
        bundle = put(o / "bundle.raucb")
        M.atomic_json(str(o / "state.json"), {**M.empty_state(), "phase": phase, "staged": {"path": str(bundle), "version": "0.4.0"}})
        assert M.cleanup_staging(str(data)) == [] and bundle.exists(), phase


def test_a_bundle_the_state_does_not_reference_is_removed(tmp_path):
    data, o = ota(tmp_path)
    bundle = put(o / "bundle.raucb")
    M.atomic_json(str(o / "state.json"), {**M.empty_state(), "phase": "staged", "staged": {"path": "/elsewhere/bundle.raucb"}})
    assert M.cleanup_staging(str(data)) == ["ota/bundle.raucb"] and not bundle.exists()


def test_the_bridge_start_keeps_a_paused_download_but_drops_an_interrupted_upload(tmp_path):
    """Without a reboot (the bridge restarted), a paused download can still be resumed; an upload never can (its connection is gone)."""
    data, o = ota(tmp_path)
    part, meta, upload = put(o / "bundle.raucb.part"), put(o / "bundle.raucb.part.json"), put(o / "bundle.raucb.upload")
    m = UpdateManager(str(data), FakeRaucClient(), trust=TRUST, version="0.3.0", log=lambda s: None, free=lambda p: 10 ** 12)
    m.resume()
    assert part.exists() and meta.exists() and not upload.exists()
    assert m.info()["download_paused"] is True


def test_cleanup_without_an_update_directory_is_a_no_op(tmp_path):
    assert M.cleanup_staging(str(tmp_path / "nothing-here")) == []


# ---------------------------------------------------------------- boot step: leftovers of interrupted atomic writes, legacy files
def test_boot_cleanup_removes_interrupted_atomic_writes_and_the_legacy_fault_file(tmp_path):
    data = tmp_path / "data"
    leftovers = [put(data / ".settings.json.tmp-123-456"), put(data / ".network.json.tmp-99"), put(data / "provision-journal.json.tmp"),
                 put(data / "session.json.tmp"), put(data / "certs" / "ca.key.tmp"), put(data / "faults.jsonl"),
                 put(data / "faults.jsonl.tmp"), put(data / "ota" / "bundle.raucb.upload")]
    keep = [put(data / "settings.json", "{}"), put(data / "secrets.json", "{}"), put(data / "network.json", "{}"),
            put(data / "session.json", "{}"), put(data / "certs" / "ca.key"), put(data / "setup-code.txt")]
    removed = storage.boot_cleanup(str(data), log=lambda m: None)
    assert not any(p.exists() for p in leftovers), removed
    assert all(p.exists() for p in keep)
    assert storage.boot_cleanup(str(data), log=lambda m: None) == []


def test_the_boot_step_runs_the_cleanup(tmp_path):
    data, boot = tmp_path / "data", tmp_path / "boot"
    boot.mkdir()
    upload = put(data / "ota" / "bundle.raucb.upload")
    legacy = put(data / "faults.jsonl")
    out = bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)
    assert not upload.exists() and not legacy.exists()
    assert "ota/bundle.raucb.upload" in out["cleanup"]


def test_a_failing_cleanup_never_stops_the_boot_step(tmp_path, monkeypatch):
    data, boot = tmp_path / "data", tmp_path / "boot"
    boot.mkdir()

    def broken(*a, **kw):
        raise OSError(5, "I/O error")
    monkeypatch.setattr(M, "cleanup_staging", broken)
    logs = []
    assert storage.boot_cleanup(str(data), log=logs.append) == []
    assert any("cleanup" in m and "OSError" in m for m in logs)
    assert bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)["cleanup"] == []


# ---------------------------------------------------------------- boot partition: no FAT write when nothing changed
def test_the_status_file_is_rewritten_only_when_its_content_changes(tmp_path, monkeypatch):
    t = [1_800_000_000]
    monkeypatch.setattr(recovery.time, "time", lambda: t[0])
    recovery.write_boot_status(str(tmp_path), ["Hi-Fi Bridge 'x'", "Device CA fingerprint (SHA-256): AA"])
    first = (tmp_path / "hifi-status.txt").read_text()
    writes = []
    real = recovery._write_atomic
    monkeypatch.setattr(recovery, "_write_atomic", lambda p, text, mode=0o644: (writes.append(p), real(p, text, mode)))
    t[0] += 3600                                                          # the next boot: same status, later time
    recovery.write_boot_status(str(tmp_path), ["Hi-Fi Bridge 'x'", "Device CA fingerprint (SHA-256): AA"])
    assert writes == [] and (tmp_path / "hifi-status.txt").read_text() == first
    recovery.write_boot_status(str(tmp_path), ["Hi-Fi Bridge 'y'", "Device CA fingerprint (SHA-256): AA"])
    assert len(writes) == 1 and "'y'" in (tmp_path / "hifi-status.txt").read_text()


def test_unchanged_status_check_tolerates_an_unreadable_old_file(tmp_path):
    (tmp_path / "hifi-status.txt").write_bytes(b"\xff\xfe garbage")
    recovery.write_boot_status(str(tmp_path), ["line"])
    assert (tmp_path / "hifi-status.txt").read_text().endswith(" line\n")


def test_no_playback_history_is_kept_on_the_data_partition(tmp_path):
    """The crash-recovery journal names only the LMS item the bridge currently owns (player + stream URL), never a title or artist,
    and it is removed when the session ends."""
    from btbridge.supervision import SessionJournal
    j = SessionJournal(str(tmp_path))
    j.record("00:04:20:aa:bb:cc", "http://bridge:8090/s/tok/1.flac")
    assert set(json.loads((tmp_path / "session.json").read_text())) == {"player", "url", "t"}
    j.clear()
    assert os.listdir(tmp_path) == []
