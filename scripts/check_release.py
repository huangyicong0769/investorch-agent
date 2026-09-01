"""Validate release metadata before building or publishing artifacts."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path

VERSION_PART = r"(?:0|[1-9]\d*)"
TAG_PATTERN = re.compile(rf"v(?P<version>{VERSION_PART}\.{VERSION_PART}\.{VERSION_PART})\Z")


def parse_args() -> argparse.Namespace:
    """Parse release validation arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="Git tag to validate, for example v0.1.0")
    parser.add_argument("--github-output", type=Path, help="Optional GitHub Actions output file")
    return parser.parse_args()


def main() -> None:
    """Validate version sources and required release notes."""
    args = parse_args()
    match = TAG_PATTERN.fullmatch(args.tag)
    if match is None:
        raise SystemExit(f"release tag must use vMAJOR.MINOR.PATCH: {args.tag}")

    version = match.group("version")
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    frontend = json.loads(Path("frontend/package.json").read_text())

    versions = {
        "pyproject.toml": project["version"],
        "frontend/package.json": frontend["version"],
    }
    mismatches = {source: value for source, value in versions.items() if value != version}
    if mismatches:
        details = ", ".join(f"{source}={value}" for source, value in mismatches.items())
        raise SystemExit(f"tag {args.tag} does not match project versions: {details}")

    notes = Path("docs/releases") / f"{version}.md"
    notes_zh = Path("docs/releases") / f"{version}.zh-CN.md"
    missing = [str(path) for path in (notes, notes_zh) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing release notes: {', '.join(missing)}")

    output = f"version={version}\nnotes={notes}\nnotes_zh={notes_zh}\n"
    if args.github_output:
        with args.github_output.open("a") as output_file:
            output_file.write(output)
    print(output, end="")


if __name__ == "__main__":
    main()
