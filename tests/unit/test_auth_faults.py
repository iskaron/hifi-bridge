"""UNIT (fake clock, real ConfigStore): the owner login's edge cases that decide whether a stolen session or a flood of bad logins gets
anywhere - back-off keyed per address family, bounded memory, expiry of dead sessions, a weak new password never replacing the old one.
Coverage package 7b-3."""
import pytest

from btbridge.config import ConfigStore
from btbridge.webui.auth import Auth, AuthError, RateLimiter, Sessions, limit_key, verify_password

OLD = "a good long passphrase"


def test_an_ipv4_client_behind_a_dual_stack_socket_shares_its_back_off_with_its_plain_address():
    assert limit_key("::ffff:192.168.1.5") == limit_key("192.168.1.5") == "192.168.1.5"
    assert limit_key("2001:db8:1:2:aaaa::1") == limit_key("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"      # one host, one /64
    assert limit_key("not an address") == "not an address"


def test_a_flood_of_failures_from_many_addresses_cannot_grow_the_table_without_bound():
    lim = RateLimiter(clock=lambda: 0.0)
    keys = [f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}" for i in range(10_050)]
    for k in keys:
        lim.fail(k)
    assert len(lim._fails) == 10_000
    assert keys[0] not in lim._fails and keys[-1] in lim._fails                      # the oldest entries are what is dropped


def test_expired_sessions_are_purged_when_the_next_one_is_created():
    now = {"t": 0.0}
    s = Sessions(idle_s=10, absolute_s=100, clock=lambda: now["t"])
    old, _ = s.create()
    now["t"] = 50.0                                                                  # idle for longer than 10 s
    fresh, _ = s.create()
    assert old not in s._s and fresh in s._s and s.get(old) is None and s.get(fresh) is not None


@pytest.fixture
def auth(tmp_path):
    store = ConfigStore(str(tmp_path))
    a = Auth(store, iterations=1000)
    a.complete_setup("c", open(a.code_file).read(), OLD)
    return a


def test_a_weak_new_password_is_refused_with_the_reasons_and_the_old_one_stays(auth):
    with pytest.raises(AuthError) as e:
        auth.change_password(OLD, "short", client="10.0.0.1")
    assert e.value.code.startswith("weak_password:") and len(e.value.code) > len("weak_password:")
    assert verify_password(OLD, auth.store.secret("admin_password_hash"))


def test_a_provisioning_setup_code_is_refused_once_the_owner_has_a_password(auth):
    with pytest.raises(AuthError) as e:
        auth.adopt_setup_code("ABCD-EFGH-JKMN")
    assert e.value.code == "already_set_up"


def test_back_off_keeps_working_after_thousands_of_failures_instead_of_overflowing():
    """Found by the flood test above: 2 ** (failures - free) overflows a float after ~1100 failures; the resulting exception skipped the
    bookkeeping, so a patient attacker (one try per back-off period, for hours) ended up with no back-off at all."""
    now = {"t": 1000.0}
    lim = RateLimiter(clock=lambda: now["t"], max_s=300.0, global_max_s=60.0)
    for _ in range(2500):
        lim.fail("203.0.113.9")                                               # no exception, also long after 2 ** 1074
    with pytest.raises(AuthError) as e:
        lim.check("203.0.113.9")
    assert e.value.code == "rate_limited" and 0 < e.value.retry_after <= 300.0
    now["t"] += 301
    lim.check("198.51.100.1")                                                # another client is not held back once the global block ends
