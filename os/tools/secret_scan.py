"""Scan an image's file trees and raw partition/card images for credentials that must never ship (Python stdlib only).

    python3 secret_scan.py [--tree DIR]... [--raw FILE]... [--json OUT] [--allow RULE:GLOB]...

--tree  an extracted file system (rootfs, boot FAT). Checks file CONTENTS and file NAMES/locations:
          * private keys in PEM/OpenSSH/PGP form (a header followed by key material, so the constant "-----BEGIN PRIVATE KEY-----"
            inside libcrypto or Python is not a finding), PuTTY keys;
          * tokens: GitHub (classic + fine-grained), GitLab, Slack, AWS access keys, generic "Authorization: Bearer"/token assignments in
            configuration files;
          * login credentials: any password hash or EMPTY password in etc/shadow, a password field other than x/*/! in etc/passwd;
          * SSH: host keys, authorized_keys, id_* files; key/credential file names (*.key, *.p12, .netrc, .git-credentials, ...);
          * Wi-Fi secrets in wpa_supplicant/hostapd configuration (psk=/wpa_passphrase= with a value).
--raw   a raw image (whole card, partition, ext4/vfat image): the content rules over every byte, including unallocated space and
        deleted-file residue. File-name rules do not apply.
Exit code 1 if anything is found (values are never printed, only the rule, location and a short fingerprint). --allow RULE:GLOB
accepts a reviewed finding (glob over the relative path); every allowance is listed in the report.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import stat
import sys

CONTENT_RULES = [
    ("private-key-pem", re.compile(rb"-----BEGIN ((?:RSA|EC|DSA|ENCRYPTED|OPENSSH|ED25519) )?PRIVATE KEY-----\r?\n"
                                   rb"(?:[A-Za-z0-9+/=:, -]{0,80}\r?\n){0,4}[A-Za-z0-9+/=]{40,}")),
    ("private-key-pgp", re.compile(rb"-----BEGIN PGP PRIVATE KEY BLOCK-----\r?\n(?:[^\r\n]{0,80}\r?\n){0,4}[A-Za-z0-9+/=]{40,}")),
    ("private-key-putty", re.compile(rb"PuTTY-User-Key-File-[23]: [a-z0-9-]+\r?\nEncryption: ")),
    ("github-token", re.compile(rb"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})")),
    ("gitlab-token", re.compile(rb"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_-]{20}")),
    ("slack-token", re.compile(rb"(?<![A-Za-z0-9_])xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("aws-access-key", re.compile(rb"(?<![A-Z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Z0-9])")),
]
# only in small text files (configuration), never in binaries: too many false positives there
TEXT_RULES = [
    ("bearer-token", re.compile(rb"(?i)authorization:\s*bearer\s+[A-Za-z0-9._~+/-]{20,}")),
    ("wifi-psk", re.compile(rb"(?m)^\s*(?:psk|wpa_passphrase|wpa_psk|sae_password)\s*=\s*\"?[^\s\"#()]{8,}\"?\s*$")),
]
# program sources assign variables with these names (psk = net.get("psk")); the Wi-Fi rule is for configuration files only
SOURCE_SUFFIXES = (".py", ".sh", ".lua", ".pl")
SUSPICIOUS_NAMES = [
    ("ssh-host-key", "*/ssh_host_*_key"), ("ssh-host-key", "*/dropbear_*_host_key"), ("ssh-authorized-keys", "*/authorized_keys*"),
    ("ssh-user-key", "*/id_rsa"), ("ssh-user-key", "*/id_ecdsa"), ("ssh-user-key", "*/id_ed25519"), ("ssh-user-key", "*/id_dsa"),
    ("key-file", "*.key"), ("key-file", "*.p12"), ("key-file", "*.pfx"), ("key-file", "*.jks"), ("key-file", "*.keystore"),
    ("credential-file", "*/.netrc"), ("credential-file", "*/.git-credentials"), ("credential-file", "*/.pgpass"),
    ("credential-file", "*/secrets/*"), ("credential-file", "*/.docker/config.json"), ("credential-file", "*/github-token"),
]
CHUNK = 8 << 20
OVERLAP = 8192
TEXT_LIMIT = 1 << 20


def fp(match: bytes) -> str:
    return hashlib.sha256(match).hexdigest()[:12]


def scan_bytes(data: bytes, text: bool, source: bool = False):
    rules = CONTENT_RULES + ([r for r in TEXT_RULES if not (source and r[0] == "wifi-psk")] if text else [])
    for name, rx in rules:
        for m in rx.finditer(data):
            yield name, m.start(), m.end(), fp(m.group(0))


def is_text(head: bytes) -> bool:
    return b"\0" not in head[:4096]


def check_shadow(path: str, rel: str):
    out = []
    for n, line in enumerate(open(path, "rb").read().decode("utf-8", "replace").splitlines(), 1):
        f = line.split(":")
        if len(f) < 2 or not f[0]:
            continue
        pw = f[1]
        if pw == "":
            out.append(("login-empty-password", rel, f"line {n} user {f[0]}"))
        elif not (pw.startswith("*") or pw.startswith("!")):
            out.append(("login-password-hash", rel, f"line {n} user {f[0]}"))
    return out


def check_passwd(path: str, rel: str):
    out = []
    for n, line in enumerate(open(path, "rb").read().decode("utf-8", "replace").splitlines(), 1):
        f = line.split(":")
        if len(f) >= 2 and f[0] and f[1] not in ("x", "*", "!", "!!"):
            out.append(("login-password-in-passwd", rel, f"line {n} user {f[0]}"))
    return out


def scan_tree(root: str):
    findings, stats = [], {"files": 0, "bytes": 0}
    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = "/" + os.path.relpath(p, root)
            for rule, glob in SUSPICIOUS_NAMES:
                if fnmatch.fnmatch(rel, glob):
                    findings.append((rule, rel, "file name"))
                    break
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            stats["files"] += 1
            stats["bytes"] += st.st_size
            if rel == "/etc/shadow":
                findings += check_shadow(p, rel)
            elif rel == "/etc/passwd":
                findings += check_passwd(p, rel)
            try:
                fh = open(p, "rb")
            except OSError as e:
                findings.append(("unreadable", rel, str(e.strerror)))
                continue
            with fh:
                head = fh.read(4096)
                text = st.st_size <= TEXT_LIMIT and is_text(head)
                fh.seek(0)
                for rule, off, f in scan_stream(fh, text, rel.endswith(SOURCE_SUFFIXES)):
                    findings.append((rule, rel, f"offset {off} sha256:{f}"))
    return findings, stats


def scan_stream(fh, text: bool, source: bool = False):
    """Chunked scan; the last OVERLAP bytes are carried over so a secret across a chunk border is still found. A match that lies
    wholly inside the carried-over tail was already reported; one rule + offset is reported once."""
    pos, prev, seen = 0, b"", set()
    while True:
        chunk = fh.read(CHUNK)
        if not chunk:
            break
        data, base = prev + chunk, pos - len(prev)
        for rule, start, end, f in scan_bytes(data, text, source):
            if end <= len(prev) or (rule, base + start) in seen:
                continue
            seen.add((rule, base + start))
            yield rule, base + start, f
        pos += len(chunk)
        prev = data[-OVERLAP:]


def dedupe(findings):
    seen, out = set(), []
    for f in findings:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", action="append", default=[])
    ap.add_argument("--raw", action="append", default=[])
    ap.add_argument("--json")
    ap.add_argument("--allow", action="append", default=[], help="RULE:GLOB of a reviewed, accepted finding")
    a = ap.parse_args(argv)
    if not a.tree and not a.raw:
        ap.error("nothing to scan")
    allow = [x.split(":", 1) for x in a.allow]
    report = {"format": "hifi-bridge-secret-scan/1", "scanned": [], "findings": [], "allowed": []}
    for t in a.tree:
        f, st = scan_tree(t)
        report["scanned"].append({"tree": t, **st})
        for rule, rel, where in dedupe(f):
            entry = {"source": t, "rule": rule, "path": rel, "where": where}
            if any(rule == r and fnmatch.fnmatch(rel, g) for r, g in allow):
                report["allowed"].append(entry)
            else:
                report["findings"].append(entry)
    for r in a.raw:
        with open(r, "rb") as fh:
            f = list(scan_stream(fh, False))
        report["scanned"].append({"raw": r, "bytes": os.path.getsize(r)})
        for rule, off, h in dedupe(f):
            report["findings"].append({"source": r, "rule": rule, "path": None, "where": f"offset {off} sha256:{h}"})
    for e in report["findings"]:
        print(f"FINDING {e['rule']}: {e['source']} {e['path'] or ''} ({e['where']})")
    for e in report["allowed"]:
        print(f"allowed {e['rule']}: {e['source']} {e['path']}")
    total = sum(s.get("files", 0) for s in report["scanned"])
    raw = sum(s.get("bytes", 0) for s in report["scanned"] if "raw" in s)
    print(f"secret scan: {total} files in {len(a.tree)} tree(s), {raw} raw bytes in {len(a.raw)} image(s): "
          f"{len(report['findings'])} finding(s), {len(report['allowed'])} allowed")
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(report, fh, indent=1)
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
