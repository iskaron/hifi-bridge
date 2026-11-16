"""Shared helpers for tests/os and tests/flashtool (run in btbridge-os: openssl, rauc, genimage, mtools, sfdisk, mkimage). STATIC/INTEGRATION of host
tools only - no board image is built and nothing is booted."""
import os
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOARD = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge")


def sh(*argv, cwd=None, env=None, check=True):
    r = subprocess.run(list(argv), cwd=cwd, env={**os.environ, **(env or {})}, capture_output=True, text=True)
    if check and r.returncode:
        raise AssertionError(f"{argv} rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


def make_ca(d, name, purpose="codeSigning", ca=True):
    """CA + signing certificate (ECDSA P-256) in directory d. Returns paths."""
    d = str(d)
    ca_key, ca_pem = f"{d}/{name}-ca.key", f"{d}/{name}-ca.pem"
    bc = "critical,CA:TRUE" if ca else "CA:FALSE"
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", ca_key, "-out", ca_pem,
       "-days", "3650", "-subj", f"/CN={name} update CA", "-addext", f"basicConstraints={bc}", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    key, csr, pem, ext = f"{d}/{name}-sign.key", f"{d}/{name}-sign.csr", f"{d}/{name}-sign.pem", f"{d}/{name}-ext"
    open(ext, "w").write(f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage={purpose}\n")
    sh("openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", key, "-out", csr, "-subj", f"/CN={name} signing")
    sh("openssl", "x509", "-req", "-in", csr, "-CA", ca_pem, "-CAkey", ca_key, "-CAcreateserial", "-out", pem, "-days", "3650", "-extfile", ext)
    return {"ca": ca_pem, "ca_key": ca_key, "cert": pem, "key": key}


@pytest.fixture
def tools():
    for t in ("openssl", "rauc", "genimage", "mtype", "sfdisk", "mkimage", "mke2fs"):
        if subprocess.run(["sh", "-c", f"command -v {t}"], capture_output=True).returncode:
            pytest.skip(f"{t} missing - run in btbridge-os (scripts/run_os.sh)")
