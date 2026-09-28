#!/usr/bin/env python3
"""Close eval PRs and delete eval branches. Prefer: python3 -m prlab_eval cleanup"""

from prlab_eval.cli import cleanup_prs

raise SystemExit(cleanup_prs())
