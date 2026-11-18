"""Entry scripts: every one that uses Docker goes through the shared helper (pre-flight, image build); the demo's Lyrion start script
presets the rooms' volume once."""
import os
import re
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT

SCRIPTS = sorted((ROOT / "scripts").glob("*.sh"))
NO_DOCKER = {"gh_api.sh", "image_pins.sh", "render_icons.py"}                 # talk to GitHub / only edit text
USES_DOCKER = re.compile(r"\bdocker\b")


def test_every_docker_script_uses_the_shared_helper():
    missing = []
    for p in SCRIPTS:
        if p.name in NO_DOCKER or p.name == "cleanup_own_images.sh":
            continue
        t = p.read_text()
        if USES_DOCKER.search(re.sub(r"^\s*#.*$", "", t, flags=re.M)) and "lib/common.sh" not in t:
            missing.append(p.name)
    assert not missing, f"no pre-flight / image helper in: {missing}"


def test_scripts_do_not_build_project_images_by_hand_any_more():
    """`docker build` of a project image belongs to scripts/lib/common.sh (dependency order, progress, one place)."""
    for p in SCRIPTS:
        if p.name in ("test_entry_clean.sh", "test_docker_versions.sh", "image_pins.sh"):
            continue
        t = re.sub(r"^\s*#.*$", "", p.read_text(), flags=re.M)
        assert "docker build" not in t, f"{p.name} builds an image itself"
        assert "compose build" not in t and "compose --profile browser build" not in t, f"{p.name} builds through compose itself"


def test_hwkit_bundle_builds_missing_images_on_a_clean_machine(fake):
    r = fake.script("scripts/hwkit_bundle.sh", "-q")
    builds = [re.search(r"-t (\S+)", c).group(1) for c in fake.calls() if c.startswith("docker build")]
    assert builds == ["btbridge-lab:dev", "btbridge-hwkit:dev"], r.stderr
    assert any(c.startswith("docker run") and "btbridge-hwkit:dev" in c for c in fake.calls())
    assert "[3/3" in r.stdout


def test_hwkit_bundle_reports_old_docker_before_building(fake):
    r = fake.script("scripts/hwkit_bundle.sh", FAKE_ENGINE="19.3.1")
    assert r.returncode == 2 and "Docker Engine is too old" in r.stderr
    assert not [c for c in fake.calls() if c.startswith("docker build")]


@pytest.fixture
def lms(tmp_path):
    cfg, seed, bindir = tmp_path / "config", tmp_path / "seed", tmp_path / "bin"
    for d in (cfg, seed, bindir):
        d.mkdir()
    for f in ("server.prefs", "favorites.opml"):
        (seed / f).write_text((ROOT / "demo/lms" / f).read_text())
    sc = bindir / "start-container"
    sc.write_text("#!/bin/sh\necho started\n")
    sc.chmod(0o755)

    def run():
        return subprocess.run(["bash", str(ROOT / "demo/lms/start.sh")], capture_output=True, text=True,
                              env={"PATH": f"{bindir}:{os.environ['PATH']}", "LMS_CONFIG_DIR": str(cfg), "LMS_SEED_DIR": str(seed)})
    return cfg, run


def volumes(cfg):
    out, cur = {}, None
    for line in (cfg / "prefs/server.prefs").read_text().splitlines():
        m = re.match(r"^_client:(\S+):$", line)
        if m:
            cur = m.group(1)
        elif not line.startswith(" "):
            cur = None
        elif cur and line.strip().startswith("volume:"):
            out[cur] = int(line.split(":")[1])
    return out


def test_fresh_demo_rooms_start_at_volume_75(lms):
    cfg, run = lms
    assert run().stdout.strip() == "started"
    assert volumes(cfg) == {f"02:00:00:00:01:0{i}": 75 for i in (1, 2, 3)}


def test_existing_demo_data_with_default_volume_is_migrated_once_and_owner_changes_are_kept(lms):
    cfg, run = lms
    (cfg / "prefs").mkdir()
    (cfg / "prefs/server.prefs").write_text("---\nwizardDone: 1\n_client:02:00:00:00:01:01:\n  volume: 50\n  other: x\n"
                                            "_client:02:00:00:00:01:02:\n  volume: 30\n_client:02:00:00:00:99:99:\n  volume: 50\nlanguage: DE\n")
    (cfg / "prefs/favorites.opml").write_text("<opml/>")
    run()
    assert volumes(cfg) == {"02:00:00:00:01:01": 75, "02:00:00:00:01:02": 30, "02:00:00:00:99:99": 50}
    assert "other: x" in (cfg / "prefs/server.prefs").read_text() and "language: DE" in (cfg / "prefs/server.prefs").read_text()
    text = (cfg / "prefs/server.prefs").read_text().replace("volume: 75", "volume: 40")      # the owner turns a room down
    (cfg / "prefs/server.prefs").write_text(text)
    run()
    assert volumes(cfg)["02:00:00:00:01:01"] == 40                                           # never touched again


def test_documented_minimum_versions_match_the_scripts():
    """README, both demo guides, the requirements page and the Makefile name the same minimum Engine/Compose as scripts/lib/common.sh."""
    src = (ROOT / "scripts/lib/common.sh").read_text()
    eng = re.search(r"HB_MIN_ENGINE=\$\{HB_MIN_ENGINE:-([0-9.]+)\}", src).group(1)
    comp = re.search(r"HB_MIN_COMPOSE=\$\{HB_MIN_COMPOSE:-([0-9.]+)\}", src).group(1)
    for f in ("README.md", "docs/DEMO.md", "docs/DEMO.de.md", "docs/DOCKER_REQUIREMENTS.md", "Makefile"):
        t = (ROOT / f).read_text()
        assert f"{eng}" in t and f"{comp}" in t, f"{f} does not state Engine {eng} / Compose {comp}"
