"""UNIT (real /bin/sh + real processes): the respawn wrapper for init systems without systemd."""
import os
import signal
import subprocess
import time

WRAPPER = os.path.join(os.path.dirname(__file__), "..", "..", "packaging", "bin", "hifi-bridge-respawn")


def test_restarts_with_backoff_logs_crashes_and_stops_on_sigterm(tmp_path):
    counter = tmp_path / "count"
    cmd = f'echo x >> {counter}; exit 3'
    env = dict(os.environ, HIFI_DATA=str(tmp_path), RESPAWN_MIN_S="1", RESPAWN_MAX_S="2", RESPAWN_HEALTHY_S="300")
    p = subprocess.Popen(["sh", WRAPPER, "sh", "-c", cmd], env=env, stderr=subprocess.PIPE, text=True)
    time.sleep(6.5)                                    # runs at ~0, 1, 3, 5 s (delays 1, 2, 2 = capped)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=5)
    runs = counter.read_text().count("x")
    log = (tmp_path / "faults-respawn.log").read_text().splitlines()
    assert 3 <= runs <= 5, runs
    assert all("exit=3" in line for line in log) and "restart_in=1s" in log[0] and "restart_in=2s" in log[1] and "restart_in=2s" in log[-1]


def test_sigterm_is_forwarded_to_the_running_service(tmp_path):
    marker = tmp_path / "got_term"
    child = f'trap "echo term > {marker}; exit 0" TERM; while true; do sleep 0.1; done'
    p = subprocess.Popen(["sh", WRAPPER, "sh", "-c", child], env=dict(os.environ, HIFI_DATA=str(tmp_path)))
    time.sleep(1.0)
    p.send_signal(signal.SIGTERM)
    assert p.wait(timeout=5) == 0
    time.sleep(0.3)
    assert marker.read_text().strip() == "term" and not (tmp_path / "faults-respawn.log").exists()
