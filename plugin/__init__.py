#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""factledger — Hermes plugin entrypoint.

Εγγράφει τα tools του ledger ως Hermes tools + το bundled skill. Καλείται
ακριβώς μία φορά κατά το startup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from . import schemas, tools  # noqa: E402


def register(ctx):
    for name, schema in schemas.SCHEMAS.items():
        handler = tools.HANDLERS.get(name)
        ctx.register_tool(
            name=name,
            toolset="factledger",
            schema=schema,
            handler=handler,
        )

    # Bundled skill: οδηγίες χρήσης του ledger μέσα σε ένα agent.
    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)


def config_defaults() -> dict:
    """Προεπιλογές διαμόρφωσης (π.χ. db_path) για το validation/doctor."""
    return {
        "db_path": tools._DB_PATH,
    }
