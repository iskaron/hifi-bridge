"""UNIT tests: owner authentication (setup code, password hashing, sessions, CSRF, rate limiting)."""
import os
import stat

import pytest

from btbridge.config import ConfigStore
from btbridge.webui.auth import (Auth, AuthError, RateLimiter, Sessions, hash_password, normalise_code, password_problems,
                                 verify_password)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def make(tmp_path, **kw):
    store = ConfigStore(str(tmp_path))
    return Auth(store, iterations=1000, **kw), store


def test_hash_verify_and_no_plaintext():
    h = hash_password("correct horse battery", iterations=1000)
    assert h.startswith("pbkdf2_sha256$1000$") and "correct" not in h
    assert verify_password("correct horse battery", h) and not verify_password("wrong", h)
    assert not verify_password("x", None) and not verify_password("x", "md5$1$a$b") and not verify_password("x", "garbage")
    assert hash_password("same", 1000) != hash_password("same", 1000)            # salted


def test_first_start_creates_a_private_setup_code_and_no_password(tmp_path):
    auth, store = make(tmp_path)
    assert not auth.has_password()
    code_file = tmp_path / "setup-code.txt"
    assert stat.S_IMODE(os.stat(code_file).st_mode) == 0o600
    code = code_file.read_text().strip()
    assert len(normalise_code(code)) == 12 and code.count("-") == 2
    assert code not in (tmp_path / "secrets.json").read_text()                   # only its hash is stored


def test_setup_requires_the_code_and_a_reasonable_password(tmp_path):
    auth, store = make(tmp_path)
    code = (tmp_path / "setup-code.txt").read_text().strip()
    with pytest.raises(AuthError, match="bad_setup_code"):
        auth.complete_setup("c1", "AAAA-BBBB-CCCC", "a good long passphrase")
    with pytest.raises(AuthError, match="weak_password:too_short"):
        auth.complete_setup("c1", code, "short")
    sid, csrf = auth.complete_setup("c1", code.lower().replace("-", " "), "a good long passphrase")   # forgiving code input
    assert auth.has_password() and auth.sessions.get(sid) and not (tmp_path / "setup-code.txt").exists()
    with pytest.raises(AuthError, match="already_set_up"):
        auth.complete_setup("c1", code, "another good passphrase")
    assert store.secret("setup_code_hash") is None


def test_login_logout_and_csrf(tmp_path):
    auth, _ = make(tmp_path)
    code = (tmp_path / "setup-code.txt").read_text().strip()
    auth.complete_setup("c", code, "a good long passphrase")
    with pytest.raises(AuthError, match="bad_password"):
        auth.login("c", "nope")
    sid, csrf = auth.login("c", "a good long passphrase")
    assert auth.sessions.check_csrf(sid, csrf) and not auth.sessions.check_csrf(sid, "x") and not auth.sessions.check_csrf("forged", csrf)
    auth.sessions.destroy(sid)
    assert auth.sessions.get(sid) is None


def test_password_change_invalidates_all_sessions(tmp_path):
    auth, _ = make(tmp_path)
    auth.complete_setup("c", (tmp_path / "setup-code.txt").read_text(), "a good long passphrase")
    s1, _ = auth.login("c", "a good long passphrase")
    with pytest.raises(AuthError):
        auth.change_password("wrong", "another fine passphrase")
    auth.change_password("a good long passphrase", "another fine passphrase")
    assert auth.sessions.get(s1) is None
    auth.login("c", "another fine passphrase")


def test_rate_limit_backs_off_per_client_and_resets_on_success():
    clk = Clock(); rl = RateLimiter(free=3, base_s=2, clock=clk)
    for _ in range(3):
        rl.check("a"); rl.fail("a")
    with pytest.raises(AuthError) as e:
        rl.check("a")
    assert e.value.code == "rate_limited" and 0 < e.value.retry_after <= 2
    rl.check("b")                                       # other clients unaffected
    clk.t += 2.1; rl.check("a"); rl.fail("a")
    with pytest.raises(AuthError) as e:
        rl.check("a")
    assert e.value.retry_after > 2                      # exponential
    clk.t += 10; rl.ok("a"); rl.check("a")


def test_login_is_rate_limited(tmp_path):
    clk = Clock()
    auth, _ = make(tmp_path, limiter=RateLimiter(free=2, clock=clk))
    auth.complete_setup("x", (tmp_path / "setup-code.txt").read_text(), "a good long passphrase")
    for _ in range(2):
        with pytest.raises(AuthError, match="bad_password"):
            auth.login("evil", "guess")
    with pytest.raises(AuthError, match="rate_limited"):
        auth.login("evil", "a good long passphrase")    # even the right password is refused while blocked


def test_sessions_expire_idle_and_absolute():
    clk = Clock(); s = Sessions(idle_s=10, absolute_s=25, clock=clk)
    sid, _ = s.create()
    clk.t += 9; assert s.get(sid)
    clk.t += 9; assert s.get(sid)                       # sliding
    clk.t += 11; assert s.get(sid) is None              # idle expiry
    sid, _ = s.create()
    for _ in range(3):
        clk.t += 9; s.get(sid)
    assert s.get(sid) is None                           # absolute lifetime 25 s


@pytest.mark.parametrize("pw,problem", [("short", "too_short"), (" leading space pw", "whitespace"), ("aaaaaaaaaaaa", "too_simple"),
                                        ("x" * 300, "too_long")])
def test_password_problems(pw, problem):
    assert problem in password_problems(pw)


def test_recovery_issues_a_new_code_and_forgets_the_password(tmp_path):
    auth, store = make(tmp_path)
    auth.complete_setup("c", (tmp_path / "setup-code.txt").read_text(), "a good long passphrase")
    sid, _ = auth.login("c", "a good long passphrase")
    code = auth.reset_with_new_setup_code()
    assert not auth.has_password() and auth.sessions.get(sid) is None and (tmp_path / "setup-code.txt").read_text().strip() == code
