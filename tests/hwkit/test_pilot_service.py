"""Private-pilot service files for a stock Pi OS Lite (UNIT / STATIC, DRY RUN only; docs/PILOT.md).

What this proves: the installer's file handling (idempotence, backups, permissions, refusals, what it would ask systemd to do), the unit
file's contents and the bounded wait helper. What it does NOT prove: that systemd on the real Pi starts the unit, that linger works there
or that audio plays under it - that is the owner's smoke test on the Pi (REAL-HARDWARE, docs/PILOT.md section 5).
No test here runs systemctl/loginctl: the installer prints `would run: ...` when HIFI_PILOT_ROOT is set.
"""
import os
import re
import stat
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PI = os.path.join(ROOT, "hwkit", "pi-setup")
SCRIPT = os.path.join(PI, "pilot_service.sh")
UNIT = os.path.join(PI, "hifi-bridge-pilot.service")
WAIT = os.path.join(PI, "hifi-bridge-pilot-wait.sh")
UNIT_NAME = "hifi-bridge-pilot.service"


def run(root, *args, model="Raspberry Pi 3 Model B Plus Rev 1.3", home=None):
    (root / "proc" / "device-tree").mkdir(parents=True, exist_ok=True)
    if model is not None:
        (root / "proc" / "device-tree" / "model").write_bytes(model.encode() + b"\0")
    home = home or "/home/pilot"
    env = {**os.environ, "HIFI_PILOT_ROOT": str(root), "HOME": home}
    return subprocess.run(["sh", SCRIPT, *args], capture_output=True, text=True, env=env, timeout=30)


def paths(root, home="/home/pilot"):
    h = root / home.lstrip("/")
    return h / ".config/systemd/user" / UNIT_NAME, h / ".config/hifi-bridge/pilot.env", h / ".config/hifi-bridge/backup", h / "bridge-data"


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if "proc" not in p.parts)


def mode(p):
    return stat.S_IMODE(p.stat().st_mode)


# --- refusals -------------------------------------------------------------------------------------------------------------------

def test_it_refuses_off_a_raspberry_pi_and_changes_nothing(tmp_path):
    r = run(tmp_path, "install", model="Some PC")
    assert r.returncode == 3 and "not a Raspberry Pi" in r.stderr
    assert not any("config" in t for t in tree(tmp_path))


def test_without_the_dry_run_root_it_never_acts_on_a_non_pi(tmp_path):
    """The development host must never get a service: off a Pi (no device-tree model) or as root the script stops before any action."""
    r = subprocess.run(["sh", SCRIPT, "install"], capture_output=True, text=True, timeout=30, env={k: v for k, v in os.environ.items() if k != "HIFI_PILOT_ROOT"})
    assert r.returncode in (3, 4) and "would run" not in r.stdout and "OK    unit written" not in r.stdout


@pytest.mark.parametrize("opt,val", [("--app-dir", "/opt/my bridge"), ("--data-dir", "/x;rm -rf /"), ("--app-dir", "relative/dir"), ("--data-dir", "")])
def test_unsafe_paths_are_rejected_before_anything_is_written(tmp_path, opt, val):
    r = run(tmp_path, "install", opt, val)
    assert r.returncode == 2 and "absolute path" in r.stderr
    assert not any("config" in t for t in tree(tmp_path))


def test_an_app_dir_without_the_bridge_code_is_refused(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run(tmp_path, "install", "--app-dir", str(empty))
    assert r.returncode == 2 and "no bridge code" in r.stderr
    assert not (tmp_path / "home").exists()


# --- install --------------------------------------------------------------------------------------------------------------------

def test_install_writes_the_unit_and_a_private_settings_file_and_does_not_start(tmp_path):
    r = run(tmp_path, "install")
    assert r.returncode == 0, r.stderr
    unit, env, backup, data = paths(tmp_path)
    text = unit.read_text()
    assert "@APP_DIR@" not in text and "@DATA_DIR@" not in text
    assert f"--data {data}" in text and f"PYTHONPATH={ROOT}/bridge:{ROOT}/hwkit" in text
    assert mode(unit) == 0o644 and mode(env) == 0o600 and mode(env.parent) == 0o700
    assert "HIFI_STREAM_TRACE" in env.read_text() and not re.search(r"^HIFI_STREAM_TRACE", env.read_text(), re.M)   # present, commented out
    out = r.stdout
    assert out.index("would run: systemctl --user daemon-reload") < out.index(f"would run: systemctl --user enable {UNIT_NAME}")
    assert "would run: loginctl enable-linger" in out
    assert "would run: systemctl --user start" not in out and "Installed and enabled, NOT started" in out
    assert not backup.exists()


def test_install_is_idempotent_and_keeps_the_owners_settings(tmp_path):
    run(tmp_path, "install")
    unit, env, backup, _ = paths(tmp_path)
    env.write_text("HIFI_STREAM_TRACE=1\n")
    before = (unit.read_text(), unit.stat().st_mtime_ns, tree(tmp_path))
    r = run(tmp_path, "install")
    assert r.returncode == 0 and "unit unchanged" in r.stdout and "settings file kept" in r.stdout
    assert (unit.read_text(), unit.stat().st_mtime_ns, tree(tmp_path)) == before
    assert env.read_text() == "HIFI_STREAM_TRACE=1\n" and not backup.exists()


def test_a_different_existing_unit_is_backed_up_before_it_is_replaced(tmp_path):
    unit, _, backup, _ = paths(tmp_path)
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/bin/true # hand-made\n")
    r = run(tmp_path, "install")
    assert r.returncode == 0 and "kept in" in r.stdout
    saved = list(backup.iterdir())
    assert len(saved) == 1 and "hand-made" in saved[0].read_text() and saved[0].name.startswith(UNIT_NAME)
    assert "hand-made" not in unit.read_text()


def test_the_existing_data_directory_is_tightened_but_its_content_is_untouched(tmp_path):
    _, _, _, data = paths(tmp_path)
    data.mkdir(parents=True)
    (data / "settings.json").write_text('{"owner": "kept"}')
    os.chmod(data, 0o755)
    os.chmod(data / "settings.json", 0o644)
    r = run(tmp_path, "install")
    assert r.returncode == 0 and "tightened from 755 to 700" in r.stdout
    assert mode(data) == 0o700 and (data / "settings.json").read_text() == '{"owner": "kept"}' and mode(data / "settings.json") == 0o644


def test_a_missing_data_directory_is_not_created_and_the_new_setup_is_announced(tmp_path):
    r = run(tmp_path, "install")
    assert not paths(tmp_path)[3].exists() and "NEW owner setup" in r.stdout


def test_install_with_start_asks_systemd_to_start_after_enabling(tmp_path):
    out = run(tmp_path, "install", "--start").stdout
    assert out.index(f"would run: systemctl --user enable {UNIT_NAME}") < out.index(f"would run: systemctl --user start {UNIT_NAME}")


# --- back to the manual launch ----------------------------------------------------------------------------------------------------

def test_disable_stops_and_prints_the_manual_launch_without_removing_anything(tmp_path):
    run(tmp_path, "install")
    before = tree(tmp_path)
    r = run(tmp_path, "disable")
    assert r.returncode == 0
    assert f"would run: systemctl --user disable --now {UNIT_NAME}" in r.stdout
    assert "python3 -u -m btbridge.appliance --data" in r.stdout and "--source bluez" in r.stdout
    assert tree(tmp_path) == before


def test_uninstall_removes_only_the_unit(tmp_path):
    run(tmp_path, "install")
    unit, env, _, data = paths(tmp_path)
    data.mkdir(parents=True)
    r = run(tmp_path, "uninstall")
    assert r.returncode == 0 and not unit.exists()
    assert env.exists() and data.exists()
    assert "Kept:" in r.stdout


# --- the unit file itself (STATIC) ------------------------------------------------------------------------------------------------

def unit_text():
    """The directives only: comments explain what is deliberately absent and must not trip the 'absent' checks."""
    return "".join(ln for ln in open(UNIT) if not ln.lstrip().startswith("#"))


def test_the_unit_matches_the_working_manual_launch_and_keeps_tracing_opt_in():
    u = unit_text()
    assert "-m btbridge.appliance" in u and "--source bluez" in u and "--data @DATA_DIR@" in u
    assert re.search(r"^ExecStart=.*python3 -u ", u, re.M)
    assert "--boot-dir" not in u                                     # stock /boot/firmware is root-owned; the pilot does not need it
    assert "HIFI_STREAM_TRACE" not in u.replace("pilot.env", "")      # opt-in via the settings file only
    assert "PYTHONPATH=@APP_DIR@/bridge:@APP_DIR@/hwkit" in u
    assert "EnvironmentFile=-%h/.config/hifi-bridge/pilot.env" in u


def test_the_unit_is_a_user_unit_that_supervises_without_rebooting_or_giving_up():
    u = unit_text()
    assert re.search(r"^Type=notify", u, re.M) and re.search(r"^WatchdogSec=\d+", u, re.M)       # READY=1 / WATCHDOG=1 are sent by the bridge
    assert re.search(r"^Restart=always", u, re.M) and re.search(r"^RestartMaxDelaySec=60", u, re.M) and "StartLimitIntervalSec=0" in u
    assert re.search(r"^WantedBy=default.target", u, re.M)                                       # user manager, not multi-user.target
    assert "pipewire.service" in u and "wireplumber.service" in u and "pipewire-pulse.service" in u
    for system_only in ("User=", "Group=", "SupplementaryGroups", "ProtectSystem", "PrivateDevices", "SystemCallFilter", "MemoryMax",
                        "reboot", "Reboot", "unsync"):
        assert not re.search(rf"^{system_only}", u, re.M), system_only
    assert "RuntimeDirectory=hifi-bridge" in u and "--run-dir %t/hifi-bridge" in u               # RAM only, nothing on the SD card
    assert "UMask=0077" in u and "NoNewPrivileges=yes" in u
    assert "network-online" not in u and "bluetooth.service" not in u                            # a user unit cannot order against system units


def test_the_pilot_files_ship_in_the_hardware_bundle():
    assert "hwkit" in open(os.path.join(ROOT, "scripts", "hwkit_bundle.sh")).read()               # whole directory
    assert "docs/PILOT.md" in open(os.path.join(ROOT, "scripts", "hwkit_bundle.sh")).read()
    assert "docs/MP3_AND_METADATA.md" in open(os.path.join(ROOT, "scripts", "hwkit_bundle.sh")).read()
    for f in (SCRIPT, WAIT):
        assert os.access(f, os.X_OK), f


# --- the bounded wait helper --------------------------------------------------------------------------------------------------------

def stub_bin(tmp_path, bluez_after=0, route=True):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    counter = tmp_path / "count"
    counter.write_text("0")
    (b / "busctl").write_text(f'#!/bin/sh\nn=$(cat "{counter}"); n=$((n+1)); echo $n > "{counter}"\n[ "$n" -gt {bluez_after} ]\n')
    (b / "ip").write_text("#!/bin/sh\n" + ('echo "default via 192.0.2.1 dev eth0"\n' if route else "exit 0\n"))
    for f in b.iterdir():
        f.chmod(0o755)
    return b


def wait_run(tmp_path, b, max_s="3"):
    env = {"PATH": f"{b}:/usr/bin:/bin", "HIFI_WAIT_MAX_S": max_s, "HIFI_WAIT_STEP_S": "0"}
    return subprocess.run(["sh", WAIT], capture_output=True, text=True, env=env, timeout=30)


def test_the_wait_helper_returns_at_once_when_bluez_and_the_route_are_up(tmp_path):
    r = wait_run(tmp_path, stub_bin(tmp_path))
    assert r.returncode == 0 and "bluez=yes default-route=yes after 0s" in r.stdout and "starting anyway" not in r.stdout


def test_the_wait_helper_waits_for_a_late_bluez(tmp_path):
    r = wait_run(tmp_path, stub_bin(tmp_path, bluez_after=2), max_s="10")
    assert r.returncode == 0 and "bluez=yes default-route=yes after 2s" in r.stdout


def test_the_wait_helper_never_fails_after_its_deadline(tmp_path):
    r = wait_run(tmp_path, stub_bin(tmp_path, bluez_after=10**6, route=False), max_s="2")
    assert r.returncode == 0 and "bluez=no default-route=no after 2s (deadline: starting anyway)" in r.stdout


# --- live MP3 test mode (DECISIONS #147): the encoder is optional, detected and measured, never installed behind the owner's back ----------

def test_mp3_check_is_read_only_prints_the_command_and_rejects_a_bad_duration(tmp_path):
    r = run(tmp_path, "mp3-check", "--seconds", "30")
    assert r.returncode == 0 and "would run: python3 -m btbridge.stream.mp3check --seconds 30" in r.stdout
    assert not any("config" in t for t in tree(tmp_path))                          # nothing written, no unit, no settings
    bad = run(tmp_path, "mp3-check", "--seconds", "30;rm")
    assert bad.returncode == 2 and "whole number" in bad.stderr


def test_the_installer_never_installs_packages_itself():
    text = open(SCRIPT).read()
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"^\s*(sudo\s+)?(apt|apt-get|dpkg)\b", code, re.M)                  # only an instruction inside a message, never a command


def test_check_explains_the_optional_encoder_without_failing_when_it_is_absent():
    text = open(SCRIPT).read()
    assert "sudo apt install lame" in text and "WAV and FLAC work without it" in text


def test_the_pilot_unit_needs_no_encoder_setting_and_keeps_icy_and_format_in_the_web_settings():
    """Format and ICY are owner settings in the data directory (WebUI), not unit/env switches: a rollback is a WebUI click or the
    unchanged previous bundle, and the unit does not change between WAV and MP3."""
    unit = open(UNIT).read()
    assert "stream_format" not in unit and "icy" not in unit.lower() and "lame" not in unit
