"""Compare two snapshot folders record by record.

    uv run python scripts/compare_snapshots.py data/output/hubspot data/output/hubspot-live

Exit 0 if they match, 1 if anything differs, 2 if a folder can't be read. See monitor/compare.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.compare import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
