"""STATIC regression tests (compose files parsed, nothing started): the lab and the demo must not be able to reach a Lyrion server - or any
service - on the Docker host or the LAN (DECISIONS #71; found when the setup wizard's discovery was answered by a real Lyrion on the dev
host). The runtime counterpart is scripts/check_isolation.sh."""
import re
from pathlib import Path

import pytest
import yaml

from conftest import ROOT

COMPOSE = {"lab": ROOT / "docker/compose.yml", "demo": ROOT / "demo/compose.yml"}
INHIBIT = "com.docker.network.bridge.inhibit_ipv4"
# The only non-internal networks and who may be on them. Lab "mdns": Docker's rules for INTERNAL networks drop multicast between containers
# (destination outside the subnet), so the mDNS interoperability suite (scripts/test_mdns.sh, profile "mdns") cannot run on one. That network
# is not internal but has no host address (inhibit_ipv4) and no masquerading: it leads nowhere. The suite checks this at runtime, too.
EDGE_ALLOWED = {"demo": {"edge": {"gateway"}}, "lab": {"mdns": {"mdns-responder", "mdns-avahi-pub", "mdns-nogw", "avahi"}}}
NO_HOST_ADDRESS_NO_NAT = {"lab": {"mdns"}}                                  # non-internal networks that must have neither a host address nor NAT
NET_ADMIN_ALLOWED = {"mdns-nogw"}                                           # deletes its own default route to prove "announces without a gateway"


def load(name):
    return yaml.safe_load(COMPOSE[name].read_text())


def attached(svc):
    n = svc.get("networks", [])
    return set(n) if isinstance(n, (list, dict)) else set()


@pytest.mark.parametrize("name", COMPOSE)
def test_every_network_is_internal_without_host_address_unless_it_is_the_gateways_edge(name):
    nets = load(name)["networks"]
    assert nets
    for net, cfg in nets.items():
        cfg = cfg or {}
        if net in EDGE_ALLOWED[name]:
            assert not cfg.get("internal", False)                          # needed for Docker to publish the gateway's ports
            if net in NO_HOST_ADDRESS_NO_NAT.get(name, ()):
                opts = {k: str(v).lower() for k, v in cfg.get("driver_opts", {}).items()}
                assert opts.get(INHIBIT) == "true", f"{name}: network {net} needs {INHIBIT}"
                assert opts.get("com.docker.network.bridge.enable_ip_masquerade") == "false", f"{name}: network {net} must not NAT"
            continue
        assert cfg.get("internal") is True, f"{name}: network {net} must be internal"
        assert str(cfg.get("driver_opts", {}).get(INHIBIT)).lower() == "true", f"{name}: network {net} needs {INHIBIT}: the host would get an address on it"


@pytest.mark.parametrize("name", COMPOSE)
def test_only_the_allowed_edge_service_is_attached_to_a_non_internal_network(name):
    doc = load(name)
    for net, allowed in EDGE_ALLOWED[name].items():
        users = {s for s, cfg in doc["services"].items() if net in attached(cfg)}
        assert users == allowed, f"{name}: network {net} is used by {users}, allowed: {allowed}"
    for s, cfg in doc["services"].items():
        assert attached(cfg), f"{name}: service {s} has no explicit network (it would land on a default, non-internal network)"
        for n in attached(cfg):
            assert n in doc["networks"], f"{name}: {s} uses undeclared network {n}"


@pytest.mark.parametrize("name", COMPOSE)
def test_nothing_gets_a_path_to_the_host(name):
    doc = load(name)
    text = COMPOSE[name].read_text()
    for s, cfg in doc["services"].items():
        assert cfg.get("network_mode") is None, f"{name}/{s}: network_mode would bypass the isolated networks"
        assert not cfg.get("extra_hosts"), f"{name}/{s}: extra_hosts (host-gateway) would point at the host"
        assert not cfg.get("privileged") and "NET_RAW" not in str(cfg.get("cap_add", ""))
        assert "NET_ADMIN" not in str(cfg.get("cap_add", "")) or (name == "lab" and s in NET_ADMIN_ALLOWED), f"{name}/{s}: NET_ADMIN"
        if s != "browser":                                                 # (the lab's browser shares the host IPC for Chromium only)
            assert not cfg.get("pid") and cfg.get("ipc") != "host"
    assert "host.docker.internal" not in text and "host-gateway" not in text
    assert "/var/run/docker.sock" not in text                              # no container can drive the host's Docker


def test_lab_publishes_no_ports_at_all():
    for s, cfg in load("lab")["services"].items():
        assert not cfg.get("ports"), f"lab/{s} publishes ports"


def test_demo_publishes_only_the_gateways_ports_and_only_to_loopback_by_default():
    for s, cfg in load("demo")["services"].items():
        if s != "gateway":
            assert not cfg.get("ports"), f"demo/{s} publishes ports"
    for p in load("demo")["services"]["gateway"]["ports"]:
        assert p.startswith("${DEMO_BIND:-127.0.0.1}:"), p


def test_lyrion_is_only_ever_addressed_as_the_compose_service_lms():
    """Every Lyrion address the lab and the demo hand to a bridge, player or helper is the service name `lms` - never an IP or host name."""
    texts = [COMPOSE[n].read_text() for n in COMPOSE]
    texts += [p.read_text() for p in (ROOT / "scripts").glob("test_*.sh")]
    for t in texts:
        for m in re.finditer(r"--(?:server|lms)\s+(?:\"|')?(?:http://)?([^\s\"',]+)", t):
            assert m.group(1).split(":")[0] == "lms", m.group(0)
    for p in list((ROOT / "tests/demo").rglob("*.py")) + list((ROOT / "tests/integration").rglob("*.py")):
        for m in re.finditer(r"""["']((?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?)["']""", p.read_text()):
            ip = m.group(1)
            assert re.match(r"(?:https?://)?(127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.255)", ip) or ip.startswith("172.29.") \
                or ip.startswith("172.30."), f"{p.name}: {ip} - a test must not point at an address outside the lab"


def test_no_compose_file_uses_features_newer_than_the_documented_minimum():
    """gw_priority needs Compose 2.33 / Engine 28 and made `make demo` fail on Engine 25 / Compose 2.24 (owner's notebook)."""
    for n, p in COMPOSE.items():
        text = p.read_text()
        assert "gw_priority" not in text.replace("`gw_priority`", ""), f"{n}: gw_priority needs Compose >= 2.33 and Engine >= 28"
        assert not re.search(r"^\s*(include|models|develop):", text, re.M), f"{n}: top-level/service feature newer than Compose 2.20"
        assert not re.search(r"^\s*(attachable|post_start|pre_stop|use_api_socket|label_file):", text, re.M)


def test_gateway_default_route_does_not_depend_on_gw_priority():
    """The gateway is attached to demo (internal) and edge (not internal); Docker uses the NON-internal network for the default route on every
    supported engine, which is what publishes the ports (verified on Engine 24.0-29.1 in docs/DOCKER_REQUIREMENTS.md)."""
    gw = load("demo")["services"]["gateway"]
    assert set(gw["networks"]) == {"demo", "edge"}


def test_the_stack_test_runs_the_runtime_isolation_check():
    t = (ROOT / "scripts/test_demo.sh").read_text()
    assert "check_isolation.sh" in t and "--allow-edge gateway" in t
    assert Path(ROOT / "scripts/check_isolation.sh").is_file()
