"""STATIC tests of the GitHub Actions workflows (parsed, nothing runs): the public-repository safety rules of ci.yml's header and the
signing rules of the macOS flash-tool workflow (DECISIONS #126, docs/MACOS_SIGNING.md). They prove the configuration, not that a macOS
runner, a certificate or the notary service work - nothing of that has run (G3 F-6)."""
import re
from pathlib import Path

import yaml

from conftest import ROOT

WF_DIR = ROOT / ".github/workflows"
WORKFLOWS = sorted(WF_DIR.glob("*.yml"))
MACOS = WF_DIR / "flashtool-macos.yml"
MACOS_DIR = ROOT / "flashtool/packaging/macos"


def load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    doc["on"] = doc.pop(True, doc.get("on"))            # YAML 1.1 reads the key `on` as boolean True
    return doc


def steps(job: dict) -> list[dict]:
    return job.get("steps", [])


def test_the_macos_workflow_exists_next_to_ci():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "flashtool-macos.yml"}


def test_every_third_party_action_is_pinned_to_a_full_commit_sha():
    for wf in WORKFLOWS:
        for name, job in load(wf)["jobs"].items():
            for st in steps(job):
                if "uses" in st:
                    assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", st["uses"]), f"{wf.name}:{name}: {st['uses']} is not SHA-pinned"


def test_no_workflow_runs_fork_code_with_secrets_and_checkouts_drop_the_token():
    for wf in WORKFLOWS:
        doc = load(wf)
        assert "pull_request_target" not in (doc["on"] or {}), wf.name
        assert doc["permissions"] == {"contents": "read"}, wf.name
        for job in doc["jobs"].values():
            for st in steps(job):
                if st.get("uses", "").startswith("actions/checkout@"):
                    assert st["with"]["persist-credentials"] is False


def test_ci_uses_no_secret_at_all():
    assert not re.search(r"\bsecrets\.[A-Za-z_]", (WF_DIR / "ci.yml").read_text().replace("run_secrets.sh", ""))
    assert "${{ secrets" not in (WF_DIR / "ci.yml").read_text()


def test_the_macos_workflow_only_starts_by_hand_and_signing_is_opt_in():
    doc = load(MACOS)
    assert list(doc["on"]) == ["workflow_dispatch"]                     # never on push / pull request: no fork, no automatic macOS minutes
    assert doc["on"]["workflow_dispatch"]["inputs"]["sign"] == {
        "description": "Sign with Developer ID and notarise (needs the macos-signing environment)", "type": "boolean", "default": False}
    sign = doc["jobs"]["sign"]
    assert sign["if"] == "${{ inputs.sign }}" and sign["needs"] == "build" and sign["environment"] == "macos-signing"


def test_secrets_appear_only_in_the_sign_job_and_only_as_step_environment():
    doc = load(MACOS)
    assert "secrets." not in yaml.safe_dump(doc["jobs"]["build"]) and "environment" not in doc["jobs"]["build"]
    used = set()
    for st in steps(doc["jobs"]["sign"]):
        assert "secrets." not in st.get("run", ""), "a secret must never be expanded inside a shell command line"
        for value in (st.get("env") or {}).values():
            used |= set(re.findall(r"secrets\.(\w+)", value))
    assert used == {"MACOS_DEVELOPER_ID_P12_BASE64", "MACOS_DEVELOPER_ID_P12_PASSWORD", "MACOS_SIGN_IDENTITY", "MACOS_NOTARY_KEY_P8_BASE64",
                    "MACOS_NOTARY_KEY_ID", "MACOS_NOTARY_ISSUER_ID"}
    guide = (ROOT / "docs/MACOS_SIGNING.md").read_text()
    assert all(name in guide for name in used)                         # the setup guide names every secret the workflow expects


def test_the_temporary_keychain_and_the_api_key_are_always_removed():
    sign = steps(load(MACOS)["jobs"]["sign"])
    names = [st.get("name", "") for st in sign]
    cleanup = sign[names.index("Remove the temporary keychain and the API key")]
    assert cleanup["if"] == "${{ always() }}" and "delete-keychain" in cleanup["run"] and "notary.p8" in cleanup["run"]
    assert names.index("Remove the temporary keychain and the API key") > names.index("Sign inside-out, notarise, staple, build the .dmg")


def test_the_macos_build_pins_equal_the_flash_tool_image():
    dockerfile = (ROOT / "docker/Dockerfile.flashtool").read_text()
    pins = dict(re.findall(r"^([A-Za-z0-9_-]+)==(\S+)$", (MACOS_DIR / "requirements.txt").read_text(), re.M))
    assert set(pins) == {"PySide6", "cryptography", "pyinstaller"}
    for pkg, ver in pins.items():
        assert f"{pkg}=={ver}" in dockerfile, f"{pkg} {ver} differs from docker/Dockerfile.flashtool"


def test_signing_is_inside_out_with_hardened_runtime_and_timestamp_never_deep():
    script = (MACOS_DIR / "sign_and_notarize.sh").read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    signing = [line for line in code.splitlines() if "codesign" in line and "--sign" in line]
    assert signing and all("--timestamp" in line for line in signing)
    assert "--options runtime" in code and "--entitlements" in code
    assert not any("--deep" in line for line in signing)               # --deep only in the final --verify
    helper, gui, bundle = (code.index(f'sign "$APP{x}"') for x in ("/Contents/MacOS/hififlash-helper", "/Contents/MacOS/hifi-bridge-flash", ""))
    assert helper < gui < bundle                                       # nested code first, the bundle last
    assert '!= Accepted' in code and "stapler staple" in code           # a rejected notarisation fails the job


def test_the_entitlements_are_empty_on_purpose():
    import plistlib
    assert plistlib.loads((MACOS_DIR / "entitlements.plist").read_bytes()) == {}
