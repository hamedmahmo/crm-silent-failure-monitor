"""Print the Makefile targets and their `## ` descriptions. Used by `make help`."""

import re
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def main() -> None:
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-zA-Z_-]+):.*?## (.*)$", line)
        if match:
            print(f"  make {match.group(1):<8} {match.group(2)}")


if __name__ == "__main__":
    main()
