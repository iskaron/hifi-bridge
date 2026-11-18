"""UNIT (fake mDNS/Avahi backends, real openssl certificates, real HTTP/TLS on localhost): regressions for review findings on local
access (reports/REVIEW_PHASE2B_EXISTING_CODE.md: H5, H6, M9, L12)."""
import pytest
from test_avahi import FakeStore

from btbridge.net import avahi as A
from btbridge.net import mdns as M
from btbridge.net.access import LocalAccess, instance_name


# ---------------------------------------------------------------- H5: device names that do not fit one DNS label
@pytest.mark.parametrize("name", ["ä" * 32, "Гостиная и кухня, второй этаж", "Wohnzimmer", "😀" * 32])
def test_instance_name_always_fits_one_dns_label(name):
    inst = instance_name(name)
    raw = inst.encode("utf-8")
    assert 0 < len(raw) <= 63 and inst.startswith("Hi-Fi Bridge (") and inst.endswith(")")
    M.enc_name(inst.replace(".", "_"))                      # the responder's encoder accepts it


def test_short_names_are_unchanged():
    assert instance_name("Wohnzimmer") == "Hi-Fi Bridge (Wohnzimmer)"


class Recording:
    def __init__(self, *a, on_name=None, log=None, fail=None):
        self.args, self.on_name, self.fail, self.started = a, on_name, fail, False

    def connect(self):
        return "avahi 0.8"

    def start(self):
        if self.fail:
            raise self.fail
        M.enc_name(self.args[1].replace(".", "_"))           # as the built-in responder does for the instance label
        self.started = True
        self.on_name("hifi-bridge.local")

    def stop(self):
        self.started = False


def _access(tmp_path, avahi_fail=None, builtin_fail=None, **store):
    made = {}

    def avahi_factory(*a, **k):
        made["avahi"] = Recording(*a, fail=avahi_fail, **k); return made["avahi"]

    def mdns_factory(*a, **k):
        made["builtin"] = Recording(*a, fail=builtin_fail, **k); return made["builtin"]
    acc = LocalAccess(FakeStore(**store), str(tmp_path), "ab12cd34", 8080, 8443, log=lambda m: None, addresses=lambda: ["192.168.1.20"],
                      mdns_factory=mdns_factory, avahi_factory=avahi_factory)
    return acc, made


def test_long_non_ascii_device_name_does_not_abort_the_builtin_responder(tmp_path):
    acc, made = _access(tmp_path, device_name="ä" * 32, mdns_backend="builtin")
    acc._start_mdns()                                         # was: ValueError (label >= 64 bytes) out of LocalAccess.start()
    assert made["builtin"].started and acc.mdns_backend == "builtin"


def test_an_avahi_error_reply_degrades_instead_of_aborting(tmp_path):
    acc, made = _access(tmp_path, avahi_fail=A.AvahiUnavailable("org.freedesktop.Avahi.InvalidServiceNameError"), mdns_backend="avahi")
    acc._start_mdns()                                         # was: AvahiUnavailable (not an OSError) out of appliance.run()
    assert acc.mdns is None and acc.mdns_backend == "unavailable"


def test_any_unexpected_mdns_error_degrades(tmp_path):
    acc, _ = _access(tmp_path, builtin_fail=ValueError("bad label"), mdns_backend="builtin")
    acc._start_mdns()
    assert acc.mdns is None and acc.mdns_backend == "unavailable"
