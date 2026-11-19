"""Fixtures for the REAL RAUC tests (scripts/run_ota.sh, image btbridge-ota): a private system-style dbus-daemon, the real `rauc service`
with the product's system.conf template (U-Boot backend on a file-backed environment, file-backed slots), throw-away signing keys and
bundles built by scripts/os_bundle.sh. Root inside the disposable container only (to run clients as other uids)."""
import os
import pwd
import shutil
import subprocess
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BOARD = os.path.join(REPO, "os", "br2-external", "board", "hifi-bridge")
COMPAT = "hifi-bridge-rpi3"
BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path={sock}</listen><auth>EXTERNAL</auth>
<!-- the default policy of Debian's /usr/share/dbus-1/system.conf (deny by default) -->
<policy context="default"><allow user="*"/><deny own="*"/><deny send_type="method_call"/>
<allow send_type="signal"/><allow send_requested_reply="true" send_type="method_return"/><allow send_requested_reply="true" send_type="error"/>
<allow receive_type="method_call"/><allow receive_type="method_return"/><allow receive_type="error"/><allow receive_type="signal"/>
<allow send_destination="org.freedesktop.DBus" send_interface="org.freedesktop.DBus"/>
<allow send_destination="org.freedesktop.DBus" send_interface="org.freedesktop.DBus.Introspectable"/>
<allow send_destination="org.freedesktop.DBus" send_interface="org.freedesktop.DBus.Properties"/></policy>
<includedir>/usr/share/dbus-1/system.d</includedir>
{own}
</busconfig>"""


def sh(*argv, env=None, check=True, **kw):
    r = subprocess.run(list(argv), capture_output=True, text=True, env={**os.environ, **(env or {})}, **kw)
    if check and r.returncode:
        raise AssertionError(f"{argv} rc={r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


@pytest.fixture(scope="session", autouse=True)
def real_tools():
    for t in ("rauc", "dbus-daemon", "fw_setenv", "mkenvimage", "openssl"):
        if not shutil.which(t):
            pytest.skip(f"{t} missing - run scripts/run_ota.sh")
    if os.geteuid() != 0:
        pytest.skip("needs root inside the disposable btbridge-ota container (scripts/run_ota.sh)")
    for name, uid in (("hifi-bridge", 990), ("someone", 1234)):
        try:
            pwd.getpwnam(name)
        except KeyError:
            sh("useradd", "-u", str(uid), "-M", "-s", "/usr/sbin/nologin", name)


def make_ca(d, name, purpose="codeSigning"):
    d = str(d)
    sh("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", f"{d}/{name}-ca.key",
       "-out", f"{d}/{name}-ca.pem", "-days", "3650", "-subj", f"/CN={name} test CA", "-addext", "basicConstraints=critical,CA:TRUE",
       "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    open(f"{d}/{name}.ext", "w").write(f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage={purpose}\n")
    sh("openssl", "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", f"{d}/{name}.key", "-out",
       f"{d}/{name}.csr", "-subj", f"/CN={name} signing")
    sh("openssl", "x509", "-req", "-in", f"{d}/{name}.csr", "-CA", f"{d}/{name}-ca.pem", "-CAkey", f"{d}/{name}-ca.key", "-CAcreateserial",
       "-out", f"{d}/{name}.pem", "-days", "3650", "-extfile", f"{d}/{name}.ext")
    return {"ca": f"{d}/{name}-ca.pem", "cert": f"{d}/{name}.pem", "key": f"{d}/{name}.key"}


def build_bundle(tmp, version="0.4.0", compatible=COMPAT, signer=None, dev_keys=None, rootfs=None, epoch="1700000000", name="b"):
    """The product's bundle script (inner part, scripts/os_bundle.sh --inside)."""
    if rootfs is None:
        rootfs = os.path.join(str(tmp), "rootfs.ext4")
        if not os.path.exists(rootfs):
            import random
            with open(rootfs, "wb") as fh:                         # incompressible but fixed: RAUC needs a squashfs > 4 KiB
                fh.write(random.Random(1).randbytes(400_000))
    out = os.path.join(str(tmp), f"out-{name}")
    args = ["bash", os.path.join(REPO, "scripts", "os_bundle.sh"), "--inside", "--rootfs", rootfs, "--compatible", compatible,
            "--version", version, "--out", out]
    args += ["--cert", signer["cert"], "--key", signer["key"]] if signer else ["--dev-keys", dev_keys]
    r = sh(*args, env={"SOURCE_DATE_EPOCH": epoch})
    return os.path.join(out, f"hifi-bridge-{compatible}-{version}.raucb"), r.stdout


def raw_bundle(tmp, signer, name, compatible=COMPAT):
    """A bundle made with plain `rauc bundle` - for signers the product script refuses on purpose (no codeSigning purpose)."""
    import random
    d = os.path.join(str(tmp), f"raw-{name}")
    os.makedirs(d)
    with open(os.path.join(d, "rootfs.ext4"), "wb") as fh:
        fh.write(random.Random(2).randbytes(400_000))
    open(os.path.join(d, "manifest.raucm"), "w").write(f"[update]\ncompatible={compatible}\nversion=0.4.0\n\n[bundle]\nformat=verity\n\n"
                                                       "[image.rootfs]\nfilename=rootfs.ext4\n")
    conf = os.path.join(str(tmp), f"raw-{name}.conf")
    open(conf, "w").write(f"[system]\ncompatible={compatible}\nbootloader=noop\n")
    out = os.path.join(str(tmp), f"raw-{name}.raucb")
    sh("rauc", f"--conf={conf}", "bundle", f"--cert={signer['cert']}", f"--key={signer['key']}", d, out)
    return out


class RealRauc:
    def __init__(self, tmp, keyring):
        import tempfile
        self.dir = str(tmp)
        self.busdir = tempfile.mkdtemp(prefix="hifi-ota-bus-", dir="/tmp")
        os.chmod(self.busdir, 0o755)                                    # clients run as other uids (policy test)
        self.sock = os.path.join(self.busdir, "bus")
        self.address = f"unix:path={self.sock}"
        own = f"<includedir>{os.path.join(BOARD, 'overlay', 'etc', 'dbus-1', 'system.d')}</includedir>"
        open(os.path.join(self.dir, "bus.conf"), "w").write(BUS_CONF.format(sock=self.sock, own=own))
        self.bus = subprocess.Popen(["dbus-daemon", "--config-file", os.path.join(self.dir, "bus.conf"), "--nofork", "--nopidfile"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while not os.path.exists(self.sock):
            assert time.time() - t0 < 5, "dbus-daemon did not start"
            time.sleep(0.02)
        for slot in ("A", "B"):
            with open(os.path.join(self.dir, f"slot{slot}.img"), "wb") as fh:
                fh.truncate(8 << 20)
        self.env_file = os.path.join(self.dir, "uboot.env")
        open(os.path.join(self.dir, "env.txt"), "w").write("BOOT_ORDER=A B\nBOOT_A_LEFT=3\nBOOT_B_LEFT=0\n")
        sh("mkenvimage", "-s", "0x4000", "-o", self.env_file, os.path.join(self.dir, "env.txt"))
        with open("/etc/fw_env.config", "w") as fh:                    # disposable container only
            fh.write(f"{self.env_file} 0x0000 0x4000\n")
        tpl = open(os.path.join(BOARD, "rauc", "system.conf.in")).read().replace("@COMPATIBLE@", COMPAT)
        tpl = (tpl.replace("/etc/rauc/keyring.pem", keyring).replace("/data/rauc", os.path.join(self.dir, "rauc-data"))
               .replace("/dev/mmcblk0p2", os.path.join(self.dir, "slotA.img")).replace("/dev/mmcblk0p3", os.path.join(self.dir, "slotB.img")))
        assert "bootloader=uboot" in tpl and "check-purpose=codesign" in tpl          # the PRODUCT's configuration, not a test variant
        self.conf = os.path.join(self.dir, "system.conf")
        open(self.conf, "w").write(tpl)
        self.log = open(os.path.join(self.dir, "rauc.log"), "w")
        self.proc = None
        self.start()

    def start(self, boot_slot="A"):
        os.makedirs(os.path.join(self.dir, "mnt"), exist_ok=True)
        self.proc = subprocess.Popen(["rauc", f"--conf={self.conf}", f"--mount={self.dir}/mnt", "service", f"--override-boot-slot={boot_slot}"],
                                     stdout=self.log, stderr=subprocess.STDOUT, env={**os.environ, "DBUS_SYSTEM_BUS_ADDRESS": self.address})
        from btbridge.ota.rauc import RaucClient, RaucUnavailable
        c = RaucClient(bus=self.address, timeout=5)
        t0 = time.time()
        while True:
            try:
                c.properties()
                break
            except RaucUnavailable:
                assert time.time() - t0 < 10, open(os.path.join(self.dir, "rauc.log")).read()
                time.sleep(0.1)
        c.close()

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)
            self.proc = None

    def env(self) -> dict:
        out = sh("fw_printenv").stdout
        return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)

    def logtext(self) -> str:
        self.log.flush()
        return open(os.path.join(self.dir, "rauc.log")).read()

    def close(self):
        self.stop()
        self.bus.terminate()
        self.bus.wait(timeout=5)
        shutil.rmtree(self.busdir, ignore_errors=True)


@pytest.fixture
def keys(tmp_path):
    d = tmp_path / "keys"
    d.mkdir()
    return {"dev": make_ca(d, "dev"), "other": make_ca(d, "other"), "mail": make_ca(d, "mail", purpose="emailProtection")}


@pytest.fixture
def rauc(tmp_path, keys):
    d = tmp_path / "rauc"
    d.mkdir()
    ring = d / "keyring.pem"          # trusted: the dev CA, and a CA whose certificate has only the e-mail purpose (refused by codesign)
    ring.write_text(open(keys["dev"]["ca"]).read() + open(keys["mail"]["ca"]).read())
    r = RealRauc(d, str(ring))
    yield r
    r.close()
