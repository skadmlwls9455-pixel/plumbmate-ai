"""Export canonical package schemas for readers; --check is a CI drift guard."""

import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    mismatches = []
    for source in sorted((root / "companion/src/plumbmate/schemas").glob("*.json")):
        target = root / "protocol" / source.name
        if args.check:
            if not target.exists() or target.read_bytes() != source.read_bytes():
                mismatches.append(source.name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
    if mismatches:
        parser.exit(1, "Schema exports differ: " + ", ".join(mismatches) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
