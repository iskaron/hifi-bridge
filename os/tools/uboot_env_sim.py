"""Corruption simulations of the redundant U-Boot environment on the boot FAT (DECISIONS #123, docs/OTA.md section 4.1). SIMULATED:
the REAL environment tools (libubootenv fw_printenv/fw_setenv compiled from the image's source + patches by build_libubootenv.sh, or any
other fw_printenv for a comparison) run on scratch FILES laid out like the image's fw_env.config; what U-Boot would load at the next
boot is computed by a port of U-Boot 2026.01's env_check_redund() (env/common.c) + file_fat_read() semantics (env/fat.c). Nothing is
booted, no block device or FAT is involved.

Every case starts from the two shipped copies (or synthetic ones), damages them, then:
  1. ``fw_printenv`` - what Linux (RAUC) reads;
  2. ``fw_setenv BOOT_B_LEFT 3`` - the first write RAUC makes when it activates slot B (a "default environment" store when nothing valid was read);
  3. both files afterwards: CRC, serial, whether the copy that was NOT written is byte-identical to before (never destroyed);
  4. which copy U-Boot loads at the next boot and whether boot.cmd then runs on a slot state (hifi_env=1) or fails safe.

    python3 uboot_env_sim.py --tool DIR/fw_printenv --copy uboot.env --copy uboot-redund.env --default u-boot-initial-env \
        [--size 0x4000] [--no-expect] [--json OUT] [--md OUT]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

NAMES = ("uboot.env", "uboot-redund.env")
SHOW = ("BOOT_ORDER", "BOOT_A_LEFT", "BOOT_B_LEFT", "hifi_env", "bootcmd")


def env_bytes(pairs: dict, flags: int, size: int = 0x4000) -> bytes:
    """A redundant-format U-Boot environment image: CRC32 (LE) + serial byte + NUL-separated pairs + 0x00 terminator + 0xff padding."""
    data = b"".join(f"{k}={v}".encode() + b"\0" for k, v in pairs.items()) + b"\0"
    data += b"\xff" * (size - 5 - len(data))
    return zlib.crc32(data).to_bytes(4, "little") + bytes([flags]) + data


def parse(raw: bytes, size: int) -> dict:
    """CRC/serial/vars of one copy as U-Boot sees it: a missing file is a read failure; a short file is read partially (the rest of
    U-Boot's buffer is undefined), so its CRC cannot match."""
    if raw is None:
        return {"read": False, "crc_ok": False, "flags": None, "vars": {}}
    data = raw[5:size]
    crc_ok = len(raw) >= size and int.from_bytes(raw[:4], "little") == zlib.crc32(data)
    out = {}
    if crc_ok:
        for item in data.split(b"\0"):
            if not item:
                break
            k, _, v = item.decode("utf-8", "replace").partition("=")
            out[k] = v
    return {"read": True, "crc_ok": crc_ok, "flags": raw[4] if len(raw) > 4 else None, "vars": out}


def uboot_choice(c1: dict, c2: dict):
    """U-Boot 2026.01 env_check_redund(): index of the copy it imports, or None (built-in default environment)."""
    if not c1["crc_ok"] and not c2["crc_ok"]:
        return None
    if c1["crc_ok"] and not c2["crc_ok"]:
        return 0
    if c2["crc_ok"] and not c1["crc_ok"]:
        return 1
    f1, f2 = c1["flags"], c2["flags"]
    if f1 == 255 and f2 == 0:
        return 1
    if f2 == 255 and f1 == 0:
        return 0
    return 1 if f2 > f1 else 0          # equal serials: the first copy (ENV_VALID)


def default_vars(path):
    out = {}
    for ln in open(path, encoding="utf-8"):
        ln = ln.rstrip("\n")
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k] = v
    return out


def _flip(raw: bytes, at: int) -> bytes:
    b = bytearray(raw)
    b[at] ^= 0xFF
    return bytes(b)


def cases(a: bytes, b: bytes, size: int):
    """(name, description, copy1, copy2) - None = file missing. a = shipped copy 1 (serial 1), b = shipped copy 2 (serial 0)."""
    newer_b = env_reserial(b, 2, size)                         # copy 2 written last (serial 2): newest
    torn = newer_b[:size // 2] + a[size // 2:]                 # half of a new write over the old data, full length
    yield "both-valid", "both copies valid (as shipped, serials 1/0)", a, b
    yield "newest-crc-bad", "newest copy (1) has a flipped byte (CRC mismatch)", _flip(a, size // 2), b
    yield "older-crc-bad", "older copy (2) has a flipped byte", a, _flip(b, size // 2)
    yield "newest-torn", "newest copy (2) half rewritten (power cut mid-write, full length, CRC mismatch)", a, torn
    yield "newest-truncated", "newest copy (1) truncated to half its size (power cut while the FAT file was rewritten)", a[:size // 2], b
    yield "newest-empty", "newest copy (1) truncated to 0 bytes", b"", b
    yield "older-truncated", "older copy (2) truncated to 1 KiB", a, b[:1024]
    yield "redundant-missing", "redundant file uboot-redund.env missing", a, None
    yield "primary-missing", "uboot.env missing", None, b
    yield "flags-equal", "both valid with the SAME serial and different content", a, env_reserial(_set(b, size, BOOT_A_LEFT="1"), 1, size)
    yield "serial-wrap", "both valid, serials 255 (copy 1) and 0 (copy 2): copy 2 is newer", env_reserial(a, 255, size), \
        env_reserial(_set(b, size, BOOT_A_LEFT="2"), 0, size)
    yield "both-crc-bad", "both copies have a flipped byte", _flip(a, size // 2), _flip(b, size // 3)
    yield "both-truncated", "both copies truncated", a[:100], b[:200]


def env_reserial(raw: bytes, flags: int, size: int) -> bytes:
    return raw[:4] + bytes([flags]) + raw[5:size]


def _set(raw: bytes, size: int, **kv) -> bytes:
    v = parse(raw, size)["vars"]
    v.update(kv)
    return env_bytes(v, raw[4], size)


def run_tool(argv, timeout=15):
    """rc/stdout/stderr; a tool that does not finish (uboot-tools 2025.10 fw_printenv spins forever on a short file) is rc 124."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"did not finish within {timeout} s (killed)"


def run_case(tool, name, desc, c1, c2, default, size, workdir):
    d = os.path.join(workdir, name)
    os.makedirs(d)
    paths = [os.path.join(d, n) for n in NAMES]
    for p, raw in zip(paths, (c1, c2)):
        if raw is not None:
            open(p, "wb").write(raw)
    conf = os.path.join(d, "fw_env.config")
    open(conf, "w").write("".join(f"{p} 0x0000 {hex(size)}\n" for p in paths))
    setenv = os.path.join(os.path.dirname(tool), "fw_setenv")
    # libubootenv takes the default environment with -f (the image uses its compiled-in /etc/u-boot-initial-env); uboot-tools has none
    fopt = ["-f", default] if default and "libubootenv" in tool_kind(tool) else []
    prc, pout, perr = run_tool([tool, "-c", conf, *fopt])
    printed = {}
    for ln in pout.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            printed[k] = v
    before = [open(p, "rb").read() if os.path.exists(p) else None for p in paths]
    src, _, serr = run_tool([setenv, "-c", conf, *fopt, "BOOT_B_LEFT", "3"])
    after = [open(p, "rb").read() if os.path.exists(p) else None for p in paths]
    p_after = [parse(x, size) for x in after]
    written = [i for i in (0, 1) if before[i] != after[i]]
    pick = uboot_choice(*p_after)
    nxt = p_after[pick]["vars"] if pick is not None else None
    return {
        "case": name, "description": desc,
        "printenv": {"rc": prc, "stderr": perr.strip()[:120], "vars": {k: printed.get(k) for k in SHOW},
                     "n_vars": len(printed)},
        "setenv": {"rc": src, "stderr": serr.strip()[:120], "written": [NAMES[i] for i in written]},
        "copies_after": [{"file": NAMES[i], "crc_ok": p_after[i]["crc_ok"], "flags": p_after[i]["flags"],
                          "size": len(after[i]) if after[i] is not None else None,
                          "unchanged": before[i] == after[i]} for i in (0, 1)],
        "uboot_next": {"copy": NAMES[pick] if pick is not None else "built-in default",
                       "vars": {k: nxt.get(k) for k in SHOW} if nxt is not None else None,
                       "boot_cmd": ("slot state" if nxt and nxt.get("hifi_env") == "1" else "fail-safe (A then B, 3 attempts each)")},
    }


def tool_kind(tool):
    rc, so, se = run_tool([tool, "-V"])
    out = (so + se).strip()
    if rc == 0 and out.split()[:1] and out.split()[0][:1].isdigit():
        return f"libubootenv {out.split()[0]}"
    return f"uboot-tools ({out.splitlines()[0][:60] if out else 'unknown'})"


def run_matrix(tool, copy1: bytes, copy2: bytes, default, size=0x4000):
    work = tempfile.mkdtemp(prefix="envsim")
    try:
        return {"tool": tool_kind(tool), "size": size,
                "results": [run_case(tool, n, d, a, b, default, size, work) for n, d, a, b in cases(copy1, copy2, size)]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def expectations(res, shipped, default):
    """[(case, ok, why)] for the PRODUCT tool (patched libubootenv + the image's Linux default environment)."""
    out = []
    dv = default_vars(default)
    full = {k: dv.get(k) for k in SHOW}
    bootcmd = shipped.get("bootcmd")
    for r in res["results"]:
        c, p, s, cp, nx = r["case"], r["printenv"], r["setenv"], r["copies_after"], r["uboot_next"]
        ok, why = True, []

        def need(cond, text):
            nonlocal ok
            if not cond:
                ok = False
                why.append(text)
        if c in ("redundant-missing", "primary-missing"):
            need(p["rc"] != 0 and s["rc"] != 0 and not s["written"], "a missing copy must make the tools fail without writing")
            need(all(x["unchanged"] for x in cp), "nothing may be changed")
            need(nx["vars"] is not None and nx["vars"].get("bootcmd") == bootcmd, "U-Boot must still find the remaining valid copy")
        elif c in ("both-crc-bad", "both-truncated"):
            need(p["rc"] == 0 and p["vars"] == full, f"fw_printenv must show the image default {full}, got {p['vars']}")
            need(s["rc"] == 0 and len(s["written"]) == 1, "fw_setenv must store ONE copy")
            need(sum(x["unchanged"] for x in cp) == 1, "the other copy must stay untouched")
            need(nx["vars"] and nx["vars"]["bootcmd"] == bootcmd and nx["vars"]["BOOT_B_LEFT"] == "3", "next boot: full env with the change")
            need(nx["boot_cmd"].startswith("fail-safe"), "an environment rebuilt from the default must make boot.cmd fail safe")
        else:
            need(p["rc"] == 0 and p["vars"].get("bootcmd") == bootcmd and p["vars"].get("hifi_env") == "1",
                 f"fw_printenv must read a valid copy, got {p['vars']}")
            need(s["rc"] == 0 and len(s["written"]) == 1, "fw_setenv must store exactly one copy")
            src = [x for x in cp if x["unchanged"]]
            need(len(src) == 1 and src[0]["crc_ok"], "the copy that was read must stay untouched and valid")
            w = [x for x in cp if not x["unchanged"]]
            need(len(w) == 1 and w[0]["crc_ok"] and w[0]["size"] == res["size"], "the written copy must be valid and full size")
            need(nx["copy"] in s["written"] and nx["vars"]["BOOT_B_LEFT"] == "3" and nx["vars"]["hifi_env"] == "1"
                 and nx["vars"]["bootcmd"] == bootcmd, "U-Boot must load the copy just written, with the slot state")
            if w and src:
                need(w[0]["flags"] == (src[0]["flags"] + 1) % 256, "the written copy must carry the next serial")
        out.append((c, ok, "; ".join(why)))
    return out


def table_md(res, exp=None):
    e = {c: (ok, why) for c, ok, why in (exp or [])}
    rows = [f"Tool: `{res['tool']}`, environment size {hex(res['size'])}.", "",
            "| Case | fw_printenv reads | fw_setenv BOOT_B_LEFT 3 writes | other copy | U-Boot next boot | boot.cmd | Result |",
            "|---|---|---|---|---|---|---|"]
    for r in res["results"]:
        p, s, n = r["printenv"], r["setenv"], r["uboot_next"]
        if p["rc"]:
            read = f"fails (rc {p['rc']}: {p['stderr'][:40]})"
        else:
            v = p["vars"]
            read = f"BOOT_ORDER={v['BOOT_ORDER']!s} A={v['BOOT_A_LEFT']!s} B={v['BOOT_B_LEFT']!s} hifi_env={v['hifi_env']!s} bootcmd={v['bootcmd']!s}"
        wr = ", ".join(s["written"]) if s["written"] else f"nothing (rc {s['rc']})"
        other = "; ".join(f"{x['file']}: {'unchanged' if x['unchanged'] else 'rewritten'}{'' if x['crc_ok'] else ' (invalid)'}"
                          for x in r["copies_after"])
        nv = n["vars"]
        nxt = f"{n['copy']}" + (f": B={nv['BOOT_B_LEFT']} hifi_env={nv['hifi_env']} bootcmd={nv['bootcmd']}" if nv else "")
        res_txt = ("PASS" if e[r["case"]][0] else "FAIL: " + e[r["case"]][1]) if r["case"] in e else "recorded"
        rows.append(f"| {r['case']}: {r['description']} | {read} | {wr} | {other} | {nxt} | {n['boot_cmd']} | {res_txt} |")
    return "\n".join(rows) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", required=True)
    ap.add_argument("--copy", action="append", required=True)
    ap.add_argument("--default", required=True)
    ap.add_argument("--size", default="0x4000")
    ap.add_argument("--no-expect", action="store_true", help="comparison run: record only")
    ap.add_argument("--json")
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    size = int(a.size, 0)
    c1, c2 = (open(p, "rb").read() for p in a.copy)
    res = run_matrix(a.tool, c1, c2, a.default, size)
    exp = None if a.no_expect else expectations(res, parse(c1, size)["vars"], a.default)
    md = table_md(res, exp)
    if a.json:
        json.dump({**res, "expectations": exp}, open(a.json, "w"), indent=1)
    if a.md:
        open(a.md, "w").write(md)
    print(md)
    return 1 if exp and not all(ok for _, ok, _ in exp) else 0


if __name__ == "__main__":
    sys.exit(main())
