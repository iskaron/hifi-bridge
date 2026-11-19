"""File-level manifest of an extracted image tree, and a comparison of two manifests (reproducibility evidence; Python stdlib only).

    python3 tree_manifest.py make DIR OUT.tsv          one line per entry: path, type, mode, uid, gid, size, sha256 or link target
    python3 tree_manifest.py compare A.tsv B.tsv [--md OUT.md]
                                                       lists added/removed/changed entries and which fields differ; exit 1 if any

Timestamps are deliberately not part of the manifest: the ext4 image hash covers them, the manifest says WHICH files differ.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys

FIELDS = ("type", "mode", "uid", "gid", "size", "content")


def entry(p):
    st = os.lstat(p)
    m = st.st_mode
    if stat.S_ISLNK(m):
        t, content = "l", os.readlink(p)
    elif stat.S_ISDIR(m):
        t, content = "d", ""
    elif stat.S_ISREG(m):
        t = "f"
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        content = h.hexdigest()
    elif stat.S_ISCHR(m) or stat.S_ISBLK(m):
        t, content = "c" if stat.S_ISCHR(m) else "b", f"{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
    else:
        t, content = "o", ""
    return t, oct(stat.S_IMODE(m)), str(st.st_uid), str(st.st_gid), str(st.st_size if t == "f" else 0), content


def make(root, out):
    root = os.path.abspath(root)
    rows = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for n in sorted(dns) + sorted(fns):
            p = os.path.join(dp, n)
            rows.append(("/" + os.path.relpath(p, root),) + entry(p))
    rows.sort()
    with open(out, "w") as fh:
        fh.write("#path\t" + "\t".join(FIELDS) + "\n")
        for r in rows:
            fh.write("\t".join(x.replace("\t", " ").replace("\n", " ") for x in r) + "\n")
    return len(rows)


def load(path):
    out = {}
    for line in open(path):
        if line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        out[f[0]] = dict(zip(FIELDS, f[1:]))
    return out


def compare(a, b):
    A, B = load(a), load(b)
    added = sorted(set(B) - set(A))
    removed = sorted(set(A) - set(B))
    changed = []
    for p in sorted(set(A) & set(B)):
        diff = [k for k in FIELDS if A[p].get(k) != B[p].get(k)]
        if diff:
            changed.append((p, diff))
    return len(A), added, removed, changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("root")
    m.add_argument("out")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--md")
    a = ap.parse_args(argv)
    if a.cmd == "make":
        print(f"{make(a.root, a.out)} entries -> {a.out}")
        return 0
    n, added, removed, changed = compare(a.a, a.b)
    lines = [f"{n} entries compared: {len(added)} added, {len(removed)} removed, {len(changed)} changed"]
    lines += [f"+ {p}" for p in added] + [f"- {p}" for p in removed] + [f"~ {p} ({', '.join(d)})" for p, d in changed]
    print("\n".join(lines[:200]))
    if a.md:
        with open(a.md, "w") as fh:
            fh.write("\n".join(["```"] + lines + ["```"]) + "\n")
    return 1 if (added or removed or changed) else 0


if __name__ == "__main__":
    sys.exit(main())
