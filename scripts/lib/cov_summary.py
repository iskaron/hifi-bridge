"""Per-package summary of a coverage.py JSON report (line AND branch), printed after the file table by scripts/run_coverage.sh.

usage: python3 cov_summary.py cov.json

Lines: statements covered / statements. Branches: branch exits covered / branch exits. "Combined" is coverage.py's own measure
(covered statements + covered branches) / (statements + branches), the number shown in the TOTAL row."""
import collections
import json
import sys


def group(path: str) -> str:
    p = path.replace("/work/", "")
    if p.startswith("bridge/btbridge/"):
        rest = p[len("bridge/btbridge/"):]
        return "btbridge/" + rest.split("/")[0] + "/" if "/" in rest else "btbridge (top-level modules)"
    return p.rsplit("/", 1)[0] + "/"


def main(path: str) -> int:
    files = json.load(open(path))["files"]
    acc = collections.defaultdict(lambda: collections.Counter())
    for name, f in files.items():
        s = f["summary"]
        a = acc[group(name)]
        a["files"] += 1
        a["stmts"] += s["num_statements"]
        a["miss"] += s["missing_lines"]
        a["br"] += s.get("num_branches", 0)
        a["brmiss"] += s.get("missing_branches", 0)
    print("\nPer package (line and branch):")
    print(f"{'package':34s} {'files':>5s} {'stmts':>6s} {'miss':>5s} {'line %':>7s} {'branches':>8s} {'missed':>6s} {'branch %':>8s} {'combined %':>10s}")
    tot = collections.Counter()
    for g in sorted(acc):
        a = acc[g]
        tot.update(a)
        line = 100.0 * (a["stmts"] - a["miss"]) / a["stmts"] if a["stmts"] else 100.0
        br = 100.0 * (a["br"] - a["brmiss"]) / a["br"] if a["br"] else 100.0
        comb = 100.0 * (a["stmts"] + a["br"] - a["miss"] - a["brmiss"]) / (a["stmts"] + a["br"]) if a["stmts"] + a["br"] else 100.0
        print(f"{g:34s} {a['files']:5d} {a['stmts']:6d} {a['miss']:5d} {line:7.1f} {a['br']:8d} {a['brmiss']:6d} {br:8.1f} {comb:10.1f}")
    a = tot
    line = 100.0 * (a["stmts"] - a["miss"]) / a["stmts"]
    br = 100.0 * (a["br"] - a["brmiss"]) / a["br"]
    comb = 100.0 * (a["stmts"] + a["br"] - a["miss"] - a["brmiss"]) / (a["stmts"] + a["br"])
    print(f"{'TOTAL':34s} {a['files']:5d} {a['stmts']:6d} {a['miss']:5d} {line:7.1f} {a['br']:8d} {a['brmiss']:6d} {br:8.1f} {comb:10.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
