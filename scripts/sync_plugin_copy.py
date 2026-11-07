#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Συγχρονίζει το standalone factledger.py μέσα στο plugin/ (μία πηγή αλήθειας).

Το Hermes plugin catalog εγκαθιστά ΜΟΝΟ το `subdir: plugin` στο pinned sha, άρα το
plugin πρέπει να κουβαλά το standalone module. Το αντίγραφο παράγεται από αυτό το
script — μην το γράφεις με το χέρι.
"""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "factledger.py"
DST = ROOT / "plugin" / "factledger.py"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not SRC.is_file():
        print(f"λείπει: {SRC}", file=sys.stderr)
        return 1
    if DST.is_file() and sha(DST) == sha(SRC):
        print("ενήμερο — καμία αλλαγή")
        return 0
    shutil.copyfile(SRC, DST)
    print(f"συγχρονίστηκε: {DST.relative_to(ROOT)} ({sha(SRC)[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
