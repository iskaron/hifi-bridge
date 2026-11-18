"""Pre-flight check (scripts/lib/common.sh) against FAKE `docker version` / `docker compose version` outputs."""
import socket

import pytest

MIN_ENGINE, MIN_COMPOSE = "24.0", "2.20"


@pytest.mark.parametrize("a,b,expected", [
    ("24.0.9", "24.0", True), ("24.0", "24.0.0", True), ("23.0.6", "24.0", False), ("25.0.2", "24.0", True), ("2.24.5", "2.20", True),
    ("2.9.9", "2.20", False), ("2.20", "2.20", True), ("2.19.9", "2.20", False), ("29.1.3", "28.0", True), ("10.0", "9.9", True),
    ("2.40.3", "2.33.1", True), ("2.33.0", "2.33.1", False),
])
def test_version_comparison_is_numeric_not_textual(fake, a, b, expected):
    r = fake.run(f'hb_version_ge "{a}" "{b}" && echo yes || echo no')
    assert r.stdout.strip() == ("yes" if expected else "no")


@pytest.mark.parametrize("raw,clean", [
    ("v2.24.5", "2.24.5"), ("2.40.3+ds1-0ubuntu1", "2.40.3"), ("29.1.3", "29.1.3"), ("v2.30.0-desktop.1", "2.30.0"),
    ("Docker Compose version v2.29.7", "2.29.7"), ("27.0.0-rc.1", "27.0.0"),
])
def test_version_strings_of_all_distributions_are_cleaned(fake, raw, clean):
    assert fake.run(f'hb_version_clean "{raw}"').stdout.strip() == clean


def test_supported_versions_pass_and_report_what_was_detected(fake):
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_ENGINE="25.0.2", FAKE_COMPOSE="2.24.5")
    assert "rc=0" in r.stdout
    assert "docker engine 25.0.2, compose 2.24.5" in r.stdout


@pytest.mark.parametrize("compose", ["2.5.1", "2.19.9", "1.29.2"])
def test_old_compose_is_refused_with_detected_required_and_upgrade_hint_in_two_languages(fake, compose):
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_COMPOSE=compose)
    assert "rc=2" in r.stdout
    err = r.stderr
    assert f"found {compose}, required {MIN_COMPOSE} or newer" in err
    assert "docker-compose-plugin" in err and "docker compose version" in err           # how to upgrade + how to check
    assert f"gefunden {compose}" in err and f"mindestens {MIN_COMPOSE}" in err           # German hint
    assert "nothing was started or changed" in err


def test_old_engine_is_refused(fake):
    r = fake.run("hb_preflight; echo rc=$?", FAKE_ENGINE="20.10.24")
    assert "rc=2" in r.stdout
    assert f"found 20.10.24, required {MIN_ENGINE} or newer" in r.stderr
    assert "gefunden 20.10.24" in r.stderr and "docs.docker.com/engine/install" in r.stderr


def test_owner_s_failing_combination_is_now_accepted_and_all_problems_are_reported_at_once(fake):
    ok = fake.run("hb_preflight --compose; echo rc=$?", FAKE_ENGINE="25.0.2", FAKE_COMPOSE="2.24.5")   # failed before (gw_priority)
    assert "rc=0" in ok.stdout
    bad = fake.run("hb_preflight --compose; echo rc=$?", FAKE_ENGINE="19.3.1", FAKE_COMPOSE="2.1.0")
    assert bad.stderr.count("PRE-FLIGHT FAILED") == 2 and "2 prerequisite(s) missing" in bad.stderr


def test_ubuntu_style_compose_version_with_distribution_suffix(fake):
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_COMPOSE="2.40.3", FAKE_COMPOSE_SUFFIX="+ds1-0ubuntu1")
    assert "rc=0" in r.stdout and "compose 2.40.3" in r.stdout


def test_missing_compose_plugin_and_legacy_v1(fake):
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_COMPOSE="")
    assert "rc=2" in r.stdout and "Docker Compose v2 is not installed" in r.stderr and "Docker Compose v2 ist nicht installiert" in r.stderr
    (fake.bin / "docker-compose").write_text("#!/bin/sh\necho 'docker-compose version 1.29.2'\n")
    (fake.bin / "docker-compose").chmod(0o755)
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_COMPOSE="")
    assert "old standalone 'docker-compose' (v1)" in r.stderr and "eigenstaendige" in r.stderr


def test_missing_docker_command(fake):
    (fake.bin / "docker").unlink()
    r = fake.run("hb_preflight; echo rc=$?", PATH=f"{fake.bin}:/usr/bin:/bin")
    assert "rc=2" in r.stdout and "Docker is not installed" in r.stderr and "Docker ist nicht installiert" in r.stderr


@pytest.mark.parametrize("mode,en,de", [
    ("down", "daemon is not reachable", "nicht erreichbar"),
    ("denied", "may not use it", "nicht verwenden"),
])
def test_daemon_down_and_permission_denied_are_told_apart(fake, mode, en, de):
    r = fake.run("hb_preflight --compose; echo rc=$?", FAKE_DAEMON=mode)
    assert "rc=2" in r.stdout and en in r.stderr and de in r.stderr
    assert r.stderr.count("PRE-FLIGHT FAILED") == 1                    # no follow-up noise about versions


def test_missing_tools_are_reported(fake):
    r = fake.run("hb_preflight --tools 'bash definitely-not-installed-tool'; echo rc=$?")
    assert "rc=2" in r.stdout and "'definitely-not-installed-tool'" in r.stderr and "'bash'" not in r.stderr


def test_disk_space_check_uses_the_project_directory(fake):
    r = fake.run('hb_preflight --disk 999999999 "the demo"; echo rc=$?')
    assert "rc=2" in r.stdout and "Not enough free disk space" in r.stderr and "Zu wenig freier Speicher" in r.stderr


def test_busy_ports_are_listed_and_the_hint_is_shown(fake):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        r = fake.run(f'hb_preflight --ports "{port} 1" "use DEMO_HTTP_PORT=28080"; echo rc=$?')
    finally:
        s.close()
    assert "rc=2" in r.stdout and f"already in use on this computer: {port}" in r.stderr and "use DEMO_HTTP_PORT=28080" in r.stderr
    assert "belegt" in r.stderr


def test_demo_script_refuses_before_touching_anything_when_compose_is_old(fake):
    r = fake.script("scripts/demo.sh", "up", FAKE_COMPOSE="2.5.1", DEMO_PROJECT="hifi-entry-never")
    assert r.returncode == 2 and "Docker Compose is too old" in r.stderr
    verbs = [c for c in fake.calls() if c.startswith("docker compose") and " up" in c or c.startswith("docker build")]
    assert verbs == []                                                  # nothing was built or started
