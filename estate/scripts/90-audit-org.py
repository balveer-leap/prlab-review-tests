#!/usr/bin/env python3
"""Leak audit for review orgs. See src/prlab_eval/audit.py for the checks.

Usage:
  90-audit-org.py ORG [ORG ...]
  90-audit-org.py --all            every org in cases/owners.json

`prlab_eval all` runs the same audit on its own before it opens or scores a PR.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from prlab_eval.audit import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
