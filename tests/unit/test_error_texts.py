"""UNIT (static, AST of the product code): every error code the code can raise, record or receive from the network agent has a German
and an English text (review N-11: codes without a text were shown raw). The codes are collected from the source, so a new code without
texts fails here: network problems (``field:reason`` of netconfig, the agent's ``{"error": ...}`` replies), update errors (UpdateError,
recorded state errors, transfer errors of downloads and uploads) and backup errors (BackupError, multipart errors of the import)."""
import ast
import os
import re

import pytest

import btbridge
from btbridge.webui import i18n

PKG = os.path.dirname(btbridge.__file__)


def tree(rel):
    with open(os.path.join(PKG, rel), encoding="utf-8") as fh:
        return ast.parse(fh.read())


def name_of(func) -> str:
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""


def first_str_args(node, names) -> set[str]:
    """First positional string argument of every call to one of ``names`` below ``node``."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and name_of(n.func) in names and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            out.add(n.args[0].value)
    return out


def dict_str_values(node, key) -> set[str]:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == key and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.add(v.value)
    return out


def err_calls(node, prefix) -> set[str]:
    """Constant codes passed to views_update._err(req, "code", ..., prefix=...)."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and name_of(n.func) == "_err" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant):
            p = next((k.value.value for k in n.keywords if k.arg == "prefix"), "upd.error.")
            if p == prefix:
                out.add(n.args[1].value)
    return out


def functions(node, names):
    return [n for n in ast.walk(node) if isinstance(n, ast.FunctionDef) and n.name in names]


def strings(node) -> list[str]:
    """Every string literal; f-strings rendered with module-level constants for their placeholders ('{}' for anything else)."""
    consts = {t.id: a.value.value for a in node.body if isinstance(a, ast.Assign) and isinstance(a.value, ast.Constant)
              for t in a.targets if isinstance(t, ast.Name)}
    inner, out = set(), []
    for n in ast.walk(node):
        if isinstance(n, ast.JoinedStr):
            parts = []
            for v in n.values:
                inner.add(id(v))
                if isinstance(v, ast.Constant):
                    parts.append(str(v.value))
                elif isinstance(v.value, ast.Name) and v.value.id in consts:
                    parts.append(str(consts[v.value.id]))
                else:
                    parts.append("{}")
            out.append("".join(parts))
    out += [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inner]
    return out


def network_reasons() -> set[str]:
    reasons = set()
    for s in strings(tree("net/netconfig.py")):                   # "file:unreadable", f"{field}:invalid", ...
        m = re.fullmatch(r"(?:[a-z_.{}]*):([a-z][a-z0-9_]*)", s)
        if m:
            reasons.add(m.group(1))
    reasons |= dict_str_values(tree("net/netagent.py"), "error")  # shown as "document:<error>"
    return reasons


def update_codes() -> set[str]:
    mgr, views, transfer = tree("ota/manager.py"), tree("webui/views_update.py"), tree("ota/transfer.py")
    codes = first_str_args(mgr, {"UpdateError", "_set_error", "abort_upload"}) | dict_str_values(mgr, "code")
    codes |= first_str_args(transfer, {"TransferError"}) - {"cancelled"}                 # a cancelled download is no error
    codes |= first_str_args(views, {"TransferError", "abort_upload"}) | err_calls(views, "upd.error.")
    return codes


def backup_codes() -> set[str]:
    views, transfer = tree("webui/views_update.py"), tree("ota/transfer.py")
    codes = first_str_args(tree("ota/backup.py"), {"BackupError"}) | err_calls(views, "bak.error.")
    for f in functions(transfer, {"boundary_of", "_disposition", "parse_multipart"}) + functions(views, {"backup_import"}):
        codes |= first_str_args(f, {"TransferError"})
    return codes


def test_the_collectors_find_the_known_codes():
    """Guard against a collector that silently finds nothing."""
    assert {"unreadable", "wrong_owner", "invalid", "required_iso_3166_alpha2", "required_12_to_63_printable_ascii", "unknown_field",
            "forbidden", "mismatch"} <= network_reasons()
    assert {"busy", "install_interrupted", "verify_failed", "reboot_timeout", "restore_failed", "digest_mismatch", "too_many_parts",
            "upload_failed"} <= update_codes()
    assert {"invalid_file", "wrong_passphrase", "too_many_parts", "field_too_large", "truncated", "timeout"} <= backup_codes()


@pytest.mark.parametrize("prefix,codes", [("net.err.", network_reasons), ("upd.error.", update_codes), ("bak.error.", backup_codes)])
def test_every_error_code_has_a_german_and_an_english_text(prefix, codes):
    en, de = i18n.strings("en"), i18n.strings("de")
    missing = sorted(f"{prefix}{c}" for c in codes() if not (en.get(prefix + c) and de.get(prefix + c)))
    assert not missing, missing


@pytest.mark.parametrize("key", ["upd.error.generic", "bak.error.generic", "upd.error.rauc_unavailable", "net.agent_unreachable.help"])
def test_generic_and_service_errors_say_what_to_do_next(key):
    """Review N-11: these used to end without a next step."""
    for lang, words in (("en", ("try again", "restart", "reload", "check")), ("de", ("erneut", "neu starten", "neu", "prüfen"))):
        text = i18n.strings(lang)[key].lower()
        assert any(w in text for w in words), (lang, key, text)
