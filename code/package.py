"""Build code.zip: README.md + code/ (no caches, data or secrets), with the evaluation reports also
placed at evaluation/ in the archive root, then check the archive.

Usage: python code/package.py [--out code.zip] [--allow-missing-reports]
"""
from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {"__pycache__", ".cache", ".pytest_cache", ".venv", "venv", "node_modules"}
EXCLUDE_SUFFIX = {".pyc", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".tmp"}
REPORTS = ["evaluation_report.md", "usage_report.md"]
SECRET = re.compile(r"AIza[0-9A-Za-z_-]{30,}|gsk_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY")
LIMIT = 50 * 1024 * 1024


def members() -> list[tuple[Path, str]]:
    out = [(ROOT / "README.md", "README.md")]
    for p in sorted((ROOT / "code").rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or set(rel.parts) & EXCLUDE_DIRS or p.suffix in EXCLUDE_SUFFIX or p.name == ".env":
            continue
        out.append((p, rel.as_posix()))
    for name in REPORTS:
        p = ROOT / "code" / "evaluation" / name
        if p.exists():
            out.append((p, f"evaluation/{name}"))
    return out


def check(path: Path, allow_missing: bool) -> list[str]:
    problems = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n in names:
            if Path(n).suffix in EXCLUDE_SUFFIX or any(part in EXCLUDE_DIRS for part in n.split("/")) \
                    or n.startswith("dataset/") or n.endswith(".env"):
                problems.append(f"forbidden member {n}")
            text = z.read(n).decode("utf-8", errors="ignore")
            if SECRET.search(text):
                problems.append(f"secret-like string in {n}")
        for req in ["README.md", "code/main.py", "code/requirements.txt"] + \
                [f"{d}/{r}" for d in ("code/evaluation", "evaluation") for r in REPORTS]:
            if req not in names and not (allow_missing and req.endswith(tuple(REPORTS))):
                problems.append(f"missing {req}")
        for r in REPORTS:
            a, b = f"code/evaluation/{r}", f"evaluation/{r}"
            if a in names and b in names and z.read(a) != z.read(b):
                problems.append(f"{a} and {b} differ")
    if path.stat().st_size > LIMIT:
        problems.append(f"archive is {path.stat().st_size} bytes (> 50 MB)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "code.zip"))
    ap.add_argument("--allow-missing-reports", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, arc in members():
            z.write(src, arc)
    problems = check(out, args.allow_missing_reports)
    with zipfile.ZipFile(out) as z:
        print(f"{out} ({out.stat().st_size:,} bytes, {len(z.namelist())} files)")
        for n in z.namelist():
            print("  ", n)
    print("\n".join(problems) or "checks passed")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
