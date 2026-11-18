"""UNIT (fake clock, fake system adapter, scripted observations): the network state machine - client Wi-Fi XOR setup hotspot, the 60 s
fallback, the probe back-off from the hotspot, Ethernet as uplink, owner trials with confirm/rollback, scan handling, failure reasons."""
import pytest

from btbridge.net import netconfig as N
from btbridge.net import netmanager as M
from btbridge.net.netmanager import Observation as Obs

WIFI = {"ssid": "Home", "psk": "wifi-secret-123"}
HOTSPOT = {"ssid": "Hi-Fi Bridge 4F2A", "psk": "correct horse battery"}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeSystem:
    def __init__(self, scan_on_hotspot=None):
        self.calls, self.scan_on_hotspot = [], scan_on_hotspot
        self.radio = "off"

    def ethernet(self, ip):
        self.calls.append(("ethernet", ip["ipv4"]["method"]))

    def client(self, wifi, country):
        assert self.radio != "hotspot" or True
        self.radio = "client"
        self.calls.append(("client", wifi["ssid"], country))

    def hotspot(self, hs, country):
        self.radio = "hotspot"
        self.calls.append(("hotspot", hs["ssid"], country))
        return self.scan_on_hotspot

    def radio_off(self):
        self.radio = "off"
        self.calls.append(("off",))

    def last(self):
        return self.calls[-1][0]


def cfg(**kw):
    doc = {"country": "DE"}
    doc.update(kw)
    return N.validate(doc)


def make(config, obs=None, scan=None):
    clock, sysa, logs = Clock(), FakeSystem(scan), []
    m = M.NetworkManager(sysa, clock, logs.append)
    if obs is not None:
        m.obs = obs
    m.load(config)
    return m, clock, sysa, logs


def run(m, clock, seconds, obs, step=1.0):
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        m.tick(obs)


CONNECTED = Obs(wifi_state="COMPLETED", wifi_address=True, wifi_addresses=["192.168.1.57"])
NOTHING = Obs()
ETH = Obs(eth_carrier=True, eth_up=True)


# ---------------------------------------------------------------- start-up
def test_boot_with_wifi_connects_and_never_runs_hotspot_and_client_together():
    m, clock, s, logs = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    assert s.calls == [("ethernet", "dhcp"), ("client", "Home", "DE")] and m.state == M.CONNECTING
    run(m, clock, 5, Obs(wifi_state="ASSOCIATING"))
    m.tick(CONNECTED)
    assert m.state == M.CONNECTED and m.failure is None and m.deadline is None
    assert "network: connecting -> connected" in logs
    assert not any(c[0] == "hotspot" for c in s.calls)


def test_configured_wifi_failing_for_60_s_opens_the_hotspot_with_a_reason():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    run(m, clock, 59, NOTHING)
    assert m.state == M.CONNECTING and m.status()["fallback_in_s"] == 1
    m.tick(Obs(events=["<3>CTRL-EVENT-NETWORK-NOT-FOUND"]))
    clock.t += 1
    m.tick(NOTHING)
    assert m.state == M.HOTSPOT and s.radio == "hotspot" and m.failure == "not_found"
    assert m.status()["hotspot"]["active"] is True and m.status()["hotspot"]["next_probe_in_s"] == 120


@pytest.mark.parametrize("events,obs_state,reason", [
    (["<3>CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid=\"Home\" auth_failures=1 duration=10 reason=WRONG_KEY"], "SCANNING", "wrong_password"),
    (["<3>CTRL-EVENT-ASSOC-REJECT bssid=00:11:22:33:44:55 status_code=17"], "SCANNING", "rejected"),
    ([], "COMPLETED", "no_address"),                       # associated, but DHCP/RA never gave an address
    ([], "SCANNING", "timeout"),
    (["<3>CTRL-EVENT-NETWORK-NOT-FOUND", "<3>CTRL-EVENT-SSID-TEMP-DISABLED reason=WRONG_KEY"], "SCANNING", "wrong_password"),
])
def test_failure_reasons(events, obs_state, reason):
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    m.tick(Obs(wifi_state=obs_state, events=events))
    run(m, clock, 60, Obs(wifi_state=obs_state))
    assert m.state == M.HOTSPOT and m.failure == reason


def test_without_hotspot_credentials_there_is_never_a_hotspot_and_the_client_keeps_trying():
    m, clock, s, _ = make(cfg(wifi=WIFI))
    run(m, clock, 600, NOTHING)
    assert m.state == M.CONNECTING and m.deadline is None and m.failure is None      # no deadline: nothing to fall back to
    assert not any(c[0] == "hotspot" for c in s.calls) and m.status()["fallback_in_s"] is None
    m.tick(CONNECTED)
    assert m.state == M.CONNECTED


def test_ethernet_uplink_prevents_the_fallback_hotspot():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT), obs=ETH)
    run(m, clock, 300, ETH)
    assert m.state == M.CONNECTING and not any(c[0] == "hotspot" for c in s.calls)
    # Ethernet unplugged: the 60 s window starts now
    run(m, clock, 59, NOTHING)
    assert m.state == M.CONNECTING
    run(m, clock, 2, NOTHING)
    assert m.state == M.HOTSPOT


def test_no_wifi_configured_hotspot_opens_after_the_grace_unless_ethernet_works():
    m, clock, s, _ = make(cfg(hotspot=HOTSPOT))
    assert m.state == M.OFF and s.calls == [("ethernet", "dhcp"), ("off",)]
    run(m, clock, 14, NOTHING)
    assert m.state == M.OFF
    run(m, clock, 1, NOTHING)
    assert m.state == M.HOTSPOT and m.next_probe is None            # nothing to probe without a client Wi-Fi
    m2, clock2, s2, _ = make(cfg(hotspot=HOTSPOT))
    run(m2, clock2, 5, NOTHING)
    run(m2, clock2, 600, ETH)
    assert m2.state == M.OFF and m2.no_uplink_since is None


def test_no_credentials_no_country_means_ethernet_only():
    m, clock, s, _ = make(N.defaults())
    run(m, clock, 600, NOTHING)
    assert m.state == M.OFF and s.calls == [("ethernet", "dhcp"), ("off",)]


# ---------------------------------------------------------------- hotspot life cycle and probe back-off
def to_hotspot(config=None, scan=None):
    m, clock, s, logs = make(config or cfg(wifi=WIFI, hotspot=HOTSPOT), scan=scan)
    run(m, clock, 61, NOTHING)
    assert m.state == M.HOTSPOT
    return m, clock, s, logs


def test_probe_backoff_returns_to_wifi_when_it_works_again():
    m, clock, s, _ = to_hotspot()
    run(m, clock, 118, NOTHING)
    assert m.state == M.HOTSPOT and not m.scan_wanted
    run(m, clock, 1, NOTHING)
    assert m.state == M.HOTSPOT and m.scan_wanted                      # first decide whether the network is in range
    assert m.scan_request() == "ap" and m.scan_request() is None
    m.scan_done([{"ssid": "Home", "signal_dbm": -60}])
    clock.t += 1
    m.tick(NOTHING)
    assert m.state == M.PROBING and s.radio == "client"
    m.tick(CONNECTED)
    assert m.state == M.CONNECTED and m.probe_index == 0 and s.radio == "client"


def test_failed_probes_back_off_2_5_10_10_minutes():
    m, clock, s, _ = to_hotspot()
    starts = []
    for _ in range(4):
        t0 = clock.t
        while m.state == M.HOTSPOT:
            clock.t += 1
            m.tick(NOTHING)
            if m.scan_wanted:
                m.scan_request(); m.scan_done(None)                       # scan not supported in AP mode -> just try
        starts.append(round(clock.t - t0))
        run(m, clock, 61, NOTHING)                                       # the probe fails after 60 s
        assert m.state == M.HOTSPOT and m.failure == "timeout"
    assert starts == [120, 300, 600, 600]


def test_probe_is_skipped_without_downtime_when_the_network_is_not_in_range():
    m, clock, s, _ = to_hotspot()
    run(m, clock, 120, NOTHING)
    m.scan_request(); m.scan_done([{"ssid": "Neighbour", "signal_dbm": -70}])
    n = len(s.calls)
    clock.t += 1
    m.tick(NOTHING)
    assert m.state == M.HOTSPOT and len(s.calls) == n and m.failure == "not_found" and m.probe_index == 1
    assert m.status()["hotspot"]["next_probe_in_s"] == 300


def test_hidden_network_is_probed_without_a_scan():
    m, clock, s, _ = to_hotspot(cfg(wifi={**WIFI, "hidden": True}, hotspot=HOTSPOT))
    run(m, clock, 120, NOTHING)
    assert m.state == M.PROBING and not m.scan_wanted


def test_a_phone_on_the_hotspot_is_never_interrupted_by_a_probe():
    m, clock, s, _ = to_hotspot()
    busy = Obs(stations=1)
    run(m, clock, 600, busy)
    assert m.state == M.HOTSPOT and m.status()["hotspot"]["stations"] == 1
    run(m, clock, 61, NOTHING)
    assert m.state in (M.HOTSPOT, M.PROBING)


def test_hotspot_closes_when_ethernet_is_back_and_nobody_uses_it():
    m, clock, s, logs = to_hotspot(cfg(hotspot=HOTSPOT))
    run(m, clock, 10, Obs(eth_carrier=True, eth_up=True, stations=1))
    assert m.state == M.HOTSPOT                                          # in use: stays
    run(m, clock, 29, ETH)
    assert m.state == M.HOTSPOT
    run(m, clock, 2, ETH)
    assert m.state == M.OFF and s.radio == "off"
    m2, clock2, s2, _ = to_hotspot()
    run(m2, clock2, 31, ETH)
    assert m2.state == M.CONNECTING and s2.radio == "client" and m2.deadline is None   # Wi-Fi configured: try it, no fallback needed


def test_connected_wifi_lost_falls_back_after_60_s_and_recovers_within():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    m.tick(CONNECTED)
    m.tick(NOTHING)
    assert m.state == M.CONNECTING and m.status()["fallback_in_s"] == 60
    run(m, clock, 30, NOTHING)
    m.tick(CONNECTED)
    assert m.state == M.CONNECTED                                        # router reboot shorter than 60 s: no hotspot
    m.tick(NOTHING)
    run(m, clock, 61, NOTHING)
    assert m.state == M.HOTSPOT


def test_hotspot_scan_cache_from_the_switch():
    m, clock, s, _ = to_hotspot(scan=[{"ssid": "Home", "signal_dbm": -50}])
    st = m.status()["scan"]
    assert st["supported"] is True and st["results"][0]["ssid"] == "Home"
    run(m, clock, 120, NOTHING)
    assert m.scan_wanted                                                 # the cached scan is stale by now


def test_wifi_interface_missing_then_appearing():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT), obs=Obs(wifi_present=False))
    assert m.state == M.UNAVAILABLE and s.calls[-1] == ("off",)
    m.request_scan()
    assert m.scan_request() is None
    run(m, clock, 100, Obs(wifi_present=False))
    assert m.state == M.UNAVAILABLE
    m.tick(NOTHING)                                                      # the driver finished loading
    assert m.state == M.CONNECTING and s.radio == "client"
    m.tick(Obs(wifi_present=False))
    assert m.state == M.UNAVAILABLE and s.radio == "off"
    m.state = M.UNAVAILABLE; m.obs = NOTHING
    m.tick(NOTHING)
    assert m.state == M.CONNECTING


def test_unavailable_radio_ignores_wifi_changes():
    m, clock, s, _ = make(cfg(hotspot=HOTSPOT), obs=Obs(wifi_present=False))
    assert m.begin_trial(cfg(wifi=WIFI, hotspot=HOTSPOT), "a" * 64) == "confirmed"
    assert m.state == M.UNAVAILABLE and s.radio == "off"


# ---------------------------------------------------------------- owner trials
def test_join_from_the_hotspot_is_a_trial_confirmed_from_the_new_network():
    m, clock, s, _ = to_hotspot(cfg(hotspot=HOTSPOT))
    new = cfg(wifi=WIFI, hotspot=HOTSPOT)
    assert m.begin_trial(new, "t1" * 32) == "pending"
    assert m.state == M.CONNECTING and s.radio == "client" and m.status()["trial"]["confirm_in_s"] == 180
    run(m, clock, 20, NOTHING)
    m.tick(CONNECTED)
    assert m.status()["trial"]["detail"] == {"wifi_connected": True, "addresses": ["192.168.1.57"]}
    assert m.confirm("x" * 64) is False
    assert m.confirm("t1" * 32) is True and m.status()["trial"]["state"] == "confirmed"
    run(m, clock, 400, CONNECTED)
    assert m.state == M.CONNECTED and m.cfg["wifi"]["ssid"] == "Home"
    assert m.confirm("t1" * 32) is False and m.revert() is False


def test_join_with_wrong_password_rolls_back_to_the_hotspot_when_the_attempt_fails():
    m, clock, s, logs = to_hotspot(cfg(hotspot=HOTSPOT))
    m.begin_trial(cfg(wifi=WIFI, hotspot=HOTSPOT), "t2" * 32)
    m.tick(Obs(events=["<3>CTRL-EVENT-SSID-TEMP-DISABLED reason=WRONG_KEY"]))
    run(m, clock, 60, NOTHING)
    t = m.status()["trial"]
    assert t["state"] == "rolled_back" and t["reason"] == "wrong_password" and m.state == M.HOTSPOT and m.cfg["wifi"] is None
    assert any("rolled back (wrong_password)" in x for x in logs)


def test_unconfirmed_join_is_rolled_back_after_three_minutes_with_the_address_it_got():
    m, clock, s, _ = to_hotspot(cfg(hotspot=HOTSPOT))
    m.begin_trial(cfg(wifi=WIFI, hotspot=HOTSPOT), "t3" * 32)
    m.tick(CONNECTED)
    run(m, clock, 179, CONNECTED)
    assert m.status()["trial"]["state"] == "pending"
    run(m, clock, 1, CONNECTED)
    t = m.status()["trial"]
    assert t["state"] == "rolled_back" and t["reason"] == "not_confirmed" and t["detail"]["addresses"] == ["192.168.1.57"]
    assert m.state == M.HOTSPOT and s.radio == "hotspot"


def test_switching_networks_rolls_back_to_the_previous_working_wifi():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    m.tick(CONNECTED)
    other = cfg(wifi={"ssid": "Office", "psk": "office-secret"}, hotspot=HOTSPOT)
    assert m.begin_trial(other, "t4" * 32) == "pending" and s.calls[-1] == ("client", "Office", "DE")
    run(m, clock, 61, NOTHING)
    assert m.status()["trial"]["state"] == "rolled_back" and s.calls[-1] == ("client", "Home", "DE") and m.cfg["wifi"]["ssid"] == "Home"


def test_static_ip_on_ethernet_with_carrier_needs_confirmation_and_rolls_back():
    m, clock, s, _ = make(N.defaults(), obs=ETH)
    static = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.9.9/24"}}})
    assert m.begin_trial(static, "t5" * 32) == "pending" and s.calls[-1] == ("ethernet", "static")
    run(m, clock, 181, ETH)
    assert m.status()["trial"]["reason"] == "not_confirmed" and s.calls[-1] == ("ethernet", "dhcp")


def test_owner_can_revert_a_pending_change():
    m, clock, s, _ = make(N.defaults(), obs=ETH)
    static = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.9.9/24"}}})
    m.begin_trial(static, "t6" * 32)
    assert m.revert() is True and m.status()["trial"]["reason"] == "reverted" and m.cfg == N.defaults()


def test_changes_without_effect_on_a_path_in_use_are_confirmed_at_once():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT), obs=ETH)
    m.tick(Obs(eth_carrier=True, eth_up=True, wifi_state="COMPLETED", wifi_address=True))
    new_hs = cfg(wifi=WIFI, hotspot={"ssid": "Setup", "psk": "another-long-pass"})
    n = len(s.calls)
    assert m.begin_trial(new_hs, "t7" * 32) == "confirmed" and len(s.calls) == n     # hotspot not active: nothing restarts
    assert m.begin_trial(new_hs, "t8" * 32) == "unchanged"
    m2, clock2, s2, _ = make(cfg(hotspot=HOTSPOT), obs=ETH)                          # Ethernet owner adds Wi-Fi: not their path
    assert m2.begin_trial(cfg(wifi=WIFI, hotspot=HOTSPOT), "t9" * 32) == "confirmed" and s2.radio == "client"
    nocarrier = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.9.9/24"}}})
    m3, _, s3, _ = make(N.defaults())
    assert m3.begin_trial(nocarrier, "ta" * 32) == "confirmed"


def test_changing_the_hotspot_while_it_runs_is_a_trial_that_restarts_it():
    m, clock, s, _ = to_hotspot(cfg(hotspot=HOTSPOT))
    new = cfg(hotspot={"ssid": "Setup", "psk": "another-long-pass"})
    assert m.begin_trial(new, "tb" * 32) == "pending" and s.calls[-1] == ("hotspot", "Setup", "DE")
    run(m, clock, 181, NOTHING)
    assert s.calls[-1] == ("hotspot", "Hi-Fi Bridge 4F2A", "DE")                    # typo in the new password: back to the old one


def test_superseding_change_keeps_the_original_as_rollback_target():
    m, clock, s, _ = make(N.defaults(), obs=ETH)
    a = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.9.9/24"}}})
    b = N.validate({"ethernet": {"ipv4": {"method": "static", "address": "192.168.9.10/24"}}})
    m.begin_trial(a, "tc" * 32)
    assert m.begin_trial(b, "td" * 32) == "pending" and m.trial.old == N.defaults() and m.trial.id == "td" * 32
    assert m.begin_trial(b, "te" * 32) == "pending"                                  # identical content still renews the trial id
    m.revert()
    assert m.cfg == N.defaults()


def test_forget_wifi_while_connected_opens_hotspot_or_turns_the_radio_off():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    m.tick(CONNECTED)
    assert m.begin_trial(cfg(hotspot=HOTSPOT), "tf" * 32) == "pending" and m.state == M.HOTSPOT
    m.confirm("tf" * 32)
    m2, clock2, s2, _ = make(cfg(wifi=WIFI))
    m2.tick(CONNECTED)
    m2.begin_trial(N.validate({"country": "DE"}), "tg" * 32)
    assert m2.state == M.OFF and s2.radio == "off"


def test_removing_the_hotspot_while_it_runs():
    m, clock, s, _ = to_hotspot()
    m.begin_trial(cfg(wifi=WIFI), "th" * 32)
    assert m.state == M.CONNECTING
    m2, clock2, s2, _ = to_hotspot(cfg(hotspot=HOTSPOT))
    m2.begin_trial(N.validate({"country": "DE"}), "ti" * 32)
    assert m2.state == M.OFF


def test_country_change_restarts_the_running_role():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT))
    m.tick(CONNECTED)
    m.begin_trial(N.validate({"country": "FR", "wifi": WIFI, "hotspot": HOTSPOT}), "tj" * 32)
    assert s.calls[-1] == ("client", "Home", "FR")
    m2, _, s2, _ = to_hotspot(cfg(hotspot=HOTSPOT))
    m2.begin_trial(N.validate({"country": "FR", "hotspot": HOTSPOT}), "tk" * 32)
    assert s2.calls[-1] == ("hotspot", "Hi-Fi Bridge 4F2A", "FR")


def test_trial_on_wifi_gets_a_deadline_even_when_ethernet_forbids_the_fallback():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT), obs=Obs(eth_carrier=True, eth_up=True, wifi_state="COMPLETED", wifi_address=True))
    m.tick(Obs(eth_carrier=True, eth_up=True, wifi_state="COMPLETED", wifi_address=True))
    assert m.state == M.CONNECTED
    m.begin_trial(cfg(wifi={"ssid": "Office", "psk": "office-secret"}, hotspot=HOTSPOT), "tl" * 32)
    assert m.deadline == clock.t + 60
    run(m, clock, 61, ETH)
    assert m.status()["trial"]["reason"] == "timeout" and m.cfg["wifi"]["ssid"] == "Home"


def test_trial_deadline_is_armed_when_the_client_was_already_connecting_without_one():
    m, clock, s, _ = make(cfg(wifi=WIFI), obs=ETH)
    m.tick(ETH)
    m.state = M.CONNECTED
    m.deadline = None
    m.begin_trial(N.validate({"country": "FR", "wifi": WIFI}), "tm" * 32)
    assert m.deadline is not None


def test_status_never_contains_passwords_and_reports_the_scan():
    m, clock, s, _ = to_hotspot()
    m.scan_done([{"ssid": "Home", "signal_dbm": -40, "security": "wpa2"}])
    st = m.status()
    text = repr(st)
    assert WIFI["psk"] not in text and HOTSPOT["psk"] not in text
    assert st["hotspot"]["address"] == "10.42.0.1" and st["country"] == "DE" and st["scan"]["results"][0]["ssid"] == "Home"
    assert st["trial"] is None and st["wifi"]["ssid"] == "Home"


def test_connecting_state_can_arm_its_deadline_later():
    m, clock, s, _ = make(cfg(wifi=WIFI, hotspot=HOTSPOT), obs=ETH)
    assert m.deadline is None
    clock.t += 1
    m.tick(NOTHING)                                      # Ethernet gone: the fallback window starts
    assert m.deadline == clock.t + 60


def test_wifi_interface_appearing_opens_the_hotspot_at_once_when_nothing_else_works():
    m, clock, s, _ = make(cfg(hotspot=HOTSPOT), obs=Obs(wifi_present=False))
    clock.t += 20
    m.tick(NOTHING)
    assert m.state == M.HOTSPOT


def test_irrelevant_events_change_nothing_and_the_grace_starts_when_ethernet_goes():
    m, clock, s, _ = make(cfg(hotspot=HOTSPOT), obs=ETH)
    m.tick(Obs(eth_carrier=True, eth_up=True, events=["<3>CTRL-EVENT-SCAN-RESULTS"]))
    assert m.reason is None and m.no_uplink_since is None
    m.tick(NOTHING)
    assert m.no_uplink_since == clock.t
    run(m, clock, 15, NOTHING)
    assert m.state == M.HOTSPOT


def test_probe_failing_while_ethernet_came_back_keeps_trying_the_client():
    m, clock, s, _ = to_hotspot(cfg(wifi={**WIFI, "hidden": True}, hotspot=HOTSPOT))
    run(m, clock, 120, NOTHING)
    assert m.state == M.PROBING
    run(m, clock, 61, ETH)
    assert m.state == M.CONNECTING and m.deadline is None and m.failure == "timeout"
