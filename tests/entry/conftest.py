"""Helpers for the entry-script tests: a FAKE `docker` command on PATH (records every call, emulates versions, images, builds), so the
shell logic of scripts/lib/common.sh and the entry scripts is tested without a Docker daemon (run by scripts/run_entry_tests.sh)."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("HB_TEST_ROOT", Path(__file__).resolve().parents[2]))

FAKE_DOCKER = r"""#!/usr/bin/env bash
# fake docker for tests/entry: state in $FAKE_STATE (images file, calls log)
echo "docker $*" >> "$FAKE_STATE/calls"
case "$1" in
  version)
    if [ "${FAKE_DAEMON:-up}" = down ]; then echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?" >&2; exit 1; fi
    if [ "${FAKE_DAEMON:-up}" = denied ]; then echo "permission denied while trying to connect to the Docker daemon socket" >&2; exit 1; fi
    echo "${FAKE_ENGINE:-29.1.3}" ;;
  compose)
    shift
    case "$1" in
      version) [ -n "${FAKE_COMPOSE-2.40.3}" ] || { echo "docker: 'compose' is not a docker command." >&2; exit 1; }
               echo "Docker Compose version ${FAKE_COMPOSE_PREFIX-v}${FAKE_COMPOSE-2.40.3}${FAKE_COMPOSE_SUFFIX:-}" ;;
      *) while [ $# -gt 0 ]; do [ "$1" = config ] && { shift; [ "$1" = --images ] && printf '%s\n' ${FAKE_COMPOSE_IMAGES:-}; exit 0; }; shift; done ;;
    esac ;;
  info) echo "${FAKE_ROOT:-/nonexistent-docker-root}" ;;
  image)
    [ "$2" = inspect ] && { grep -qxF "$3" "$FAKE_STATE/images" 2>/dev/null; exit $?; } ;;
  build)
    tag=""; prev=""; for a in "$@"; do [ "$prev" = -t ] && tag=$a; prev=$a; done
    echo "#1 [internal] load build definition from Dockerfile"; echo "#1 DONE 0.0s"
    echo "#5 [2/3] RUN apt-get update"; echo "#5 0.321 Get:1 http://deb.debian.org/debian trixie InRelease"
    [ -n "${FAKE_BUILD_SLEEP:-}" ] && sleep "$FAKE_BUILD_SLEEP"
    [ "${FAKE_BUILD_FAIL:-}" = "$tag" ] && { echo "ERROR: failed to solve: process did not complete successfully" >&2; exit 1; }
    echo "#7 naming to docker.io/library/$tag"
    echo "$tag" >> "$FAKE_STATE/images" ;;
  pull)
    echo "9.1.2: Pulling from lmscommunity/lyrionmusicserver"; echo "abc123def456: Pull complete"; echo "Status: Downloaded newer image for $2"
    echo "$2" >> "$FAKE_STATE/images" ;;
esac
exit 0
"""


@pytest.fixture
def fake(tmp_path):
    """run(snippet, **env): bash code after sourcing scripts/lib/common.sh with the fake docker first on PATH."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "images").write_text("")
    d = bindir / "docker"
    d.write_text(FAKE_DOCKER)
    d.chmod(d.stat().st_mode | stat.S_IEXEC)

    def base_env(extra):
        e = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path), "FAKE_STATE": str(state), "HB_LOGDIR": str(tmp_path / "logs"),
             "LC_ALL": "C", "TMPDIR": str(tmp_path)}
        e.update({k: str(v) for k, v in extra.items()})
        return e

    class Runner:
        bin = bindir
        state_dir = state
        tmp = tmp_path

        def images(self, *names):
            (state / "images").write_text("".join(n + "\n" for n in names))

        def calls(self):
            p = state / "calls"
            return p.read_text().splitlines() if p.exists() else []

        def run(self, snippet, **env):
            code = f". {ROOT}/scripts/lib/common.sh\n{textwrap.dedent(snippet)}"
            return subprocess.run(["bash", "-c", code], capture_output=True, text=True, env=base_env(env), cwd=ROOT, timeout=90)

        def script(self, rel, *args, **env):
            """Runs a script of a COPY of scripts/ (the repository is mounted read-only in the test container; scripts write .demo/, dist/)."""
            proj = tmp_path / "proj"
            if not proj.exists():
                shutil.copytree(ROOT / "scripts", proj / "scripts")
            return subprocess.run(["bash", str(proj / rel), *args], capture_output=True, text=True, env=base_env(env), cwd=proj, timeout=90)

    return Runner()
