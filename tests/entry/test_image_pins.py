"""Reproducible third-party images (OPEN_QUESTIONS #48, DECISIONS #73): every third-party image is referenced as <name:tag>@sha256:<digest> that
matches docker/image-pins.txt; unpinned references and floating tags are rejected."""
import re
import subprocess

import pytest
import yaml

from conftest import ROOT

LOCK = ROOT / "docker/image-pins.txt"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
LOCAL = re.compile(r"^(btbridge-[a-z0-9]+|hifi-demo)(:[A-Za-z0-9._-]+)?$")         # built from this repository
FLOATING = {"latest", "stable", "rc", "dev", "edge", "main", "master", "nightly", "testing"}


def pins():
    out = {}
    for line in LOCK.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            ref, digest, *_ = line.split()
            out[ref] = digest
    return out


def scanned_files():
    f = [ROOT / "docker/compose.yml", ROOT / "demo/compose.yml", ROOT / "demo/Dockerfile", ROOT / "Makefile"]
    f += sorted((ROOT / "docker").glob("Dockerfile.*")) + sorted((ROOT / "scripts").glob("*.sh")) + sorted((ROOT / "scripts/lib").glob("*.sh"))
    f += sorted((ROOT / "tests/entry/docker_matrix").glob("*"))
    return [p for p in f if p.is_file()]


def test_lock_file_is_wellformed_and_names_exact_versions():
    p = pins()
    assert len(p) >= 12
    for ref, digest in p.items():
        assert DIGEST.match(digest), ref
        tag = ref.rsplit(":", 1)[1]
        assert tag not in FLOATING, f"{ref}: pin an exact version tag, not a moving one"


def test_lyrion_is_pinned_to_an_exact_version():
    assert "lmscommunity/lyrionmusicserver:9.1.2" in pins()
    assert not any(r.startswith("lmscommunity/lyrionmusicserver:") and r.endswith((":stable", ":latest")) for r in pins())


@pytest.mark.parametrize("ref", sorted(pins()))
def test_every_reference_carries_the_locked_digest(ref):
    digest = pins()[ref]
    pat = re.compile(r"(?<![A-Za-z0-9./_@-])" + re.escape(ref) + r"(@sha256:[0-9a-f]{64})?(?![A-Za-z0-9._-])")
    for f in scanned_files():
        for m in pat.finditer(f.read_text()):
            assert m.group(1) == "@" + digest, f"{f.relative_to(ROOT)}: {ref} must be written {ref}@{digest}"


def compose_images():
    for name in ("docker/compose.yml", "demo/compose.yml"):
        for svc, cfg in yaml.safe_load((ROOT / name).read_text())["services"].items():
            if "image" in cfg:
                yield f"{name}:{svc}", cfg["image"]


def test_compose_images_are_pinned_or_project_images():
    for where, image in compose_images():
        if LOCAL.match(image):
            continue
        name_tag, _, digest = image.partition("@")
        assert DIGEST.match(digest), f"{where}: {image} is not pinned by digest"
        assert name_tag in pins(), f"{where}: {name_tag} is missing in docker/image-pins.txt"


def test_dockerfiles_start_from_pinned_or_project_images():
    for f in list((ROOT / "docker").glob("Dockerfile.*")) + [ROOT / "demo/Dockerfile"]:
        text = f.read_text()
        args = dict(re.findall(r"^ARG (\w+)=(\S+)", text, re.M))
        for m in re.finditer(r"^FROM\s+(\S+)", text, re.M):
            ref = m.group(1)
            ref = re.sub(r"\$\{(\w+)\}", lambda a: args.get(a.group(1), a.group(0)), ref)
            if LOCAL.match(ref):
                continue
            assert "@sha256:" in ref and ref.split("@")[0] in pins(), f"{f.name}: FROM {ref} is not pinned by the lock file"


def test_script_references_to_registry_images_are_pinned():
    """Any <registry-or-owner>/<name>:<tag> or official-image reference that is run or pulled by a script must be pinned."""
    official = r"(?:debian|alpine|python|ubuntu|docker|busybox|node|eclipse-mosquitto)"
    pat = re.compile(r"(?<![A-Za-z0-9./_@$-])((?:[a-z0-9.-]+\.[a-z]{2,}/)?(?:[a-z0-9_.-]+/[a-z0-9_.-]+|" + official + r"):[vV]?[0-9][A-Za-z0-9._-]*)(?![A-Za-z0-9._@-])")
    for f in scanned_files():
        if f.suffix != ".sh":
            continue
        for ln, line in enumerate(f.read_text().splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for m in pat.finditer(line):
                ref = m.group(1)
                after = line[m.end(1): m.end(1) + 8]
                if after.startswith("@sha256:"):
                    continue
                # not an image: paths ("/tmp/x:1"), volume specs ("$PWD":/w:ro) and similar are excluded by the lookbehind above
                assert False, f"{f.relative_to(ROOT)}:{ln}: unpinned image reference {ref}"


def test_pin_tool_check_agrees():
    r = subprocess.run(["bash", str(ROOT / "scripts/image_pins.sh"), "check"], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
