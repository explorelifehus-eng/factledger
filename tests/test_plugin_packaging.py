# -*- coding: utf-8 -*-
"""Το plugin πρέπει να είναι ΑΥΤΟΔΥΝΑΜΟ όταν εγκατασταθεί από τον κατάλογο.

Ο κατάλογος του Hermes κατεβάζει μόνο το `subdir: plugin` στο pinned sha — όχι το
repo root. Άρα το standalone `factledger.py` πρέπει να υπάρχει ΜΕΣΑ στο plugin (ή
το plugin να μη το χρειάζεται). Αυτά τα tests κλειδώνουν και τα δύο:
  1. το αντίγραφο δεν ξεφεύγει από την πηγή (byte-identical),
  2. σε layout εγκατάστασης το `tools.fl.Ledger` υπάρχει ΧΩΡΙΣ FACTLEDGER_PY.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_plugin_ships_standalone_copy_in_sync():
    shipped = PLUGIN / "factledger.py"
    assert shipped.is_file(), "λείπει το plugin/factledger.py — ο κατάλογος δεν θα το βρει"
    assert _sha(shipped) == _sha(ROOT / "factledger.py"), (
        "το plugin/factledger.py έχει ξεφύγει από το factledger.py — "
        "τρέξε: python scripts/sync_plugin_copy.py"
    )


def test_tools_load_in_install_layout_without_env(tmp_path):
    """Layout = ό,τι βλέπει το `hermes plugins install`: μόνο τα περιεχόμενα του plugin/."""
    site = tmp_path / "site"
    pkg = site / "factledger"
    shutil.copytree(PLUGIN, pkg, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    code = (
        "import importlib.util, sys\n"
        f"sys.path.insert(0, r'{site}')\n"
        f"spec = importlib.util.spec_from_file_location('plug_tools', r'{pkg / 'tools.py'}')\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "assert hasattr(m.fl, 'Ledger'), 'fl.Ledger λείπει (import collision ή μη συσκευασμένο factledger.py)'\n"
        "print('OK')\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in ("FACTLEDGER_PY", "FACTLEDGER_DB")}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr
