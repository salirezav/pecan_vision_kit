<<<<<<< HEAD
#!/usr/bin/env python3
"""Copy models shipped in models/ into the dashboard model library.

Asks before copying unless --yes is passed. A model already in the library
is left unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from segment import ModelService  # noqa: E402
from segment_inspect import STORE_DIR  # noqa: E402


def format_size(num: int) -> str:
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num} B"


def library_ready() -> bool:
    return STORE_DIR.exists() or STORE_DIR.parent.exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy models shipped with this release into the dashboard library.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Copy without asking",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show shipped models that are not in the library yet",
    )
    args = parser.parse_args(argv)

    if not library_ready():
        print(
            f"The model library drive is not available ({STORE_DIR}).\n"
            "On a kit this is /mnt/recordings. Open the dashboard there and choose Add models,\n"
            "or run this script on the kit.",
            file=sys.stderr,
        )
        return 1

    service = ModelService(ROOT, store_dir=STORE_DIR)
    missing = [row for row in service.bundled_catalog() if not row["installed"]]
    if not missing:
        print("Every model shipped with this release is already in the dashboard library.")
        print(f"Library: {service.library_dir()}")
        return 0

    print("New models are available to use:")
    for row in missing:
        note = " — previously skipped on the dashboard" if row["dismissed"] else ""
        print(f"  - {row['filename']} ({format_size(int(row['bytes']))}){note}")
    print(f"Destination: {service.library_dir()}")
    print("Models already in the library are left as they are.")
    if args.list:
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("Nothing was copied. Run this script in a terminal and answer yes, or pass --yes.")
            return 0
        try:
            answer = input("Copy these models into the dashboard library? [y/N] ")
        except EOFError:
            print("Nothing was copied.")
            return 0
        if answer.strip().lower() not in {"y", "yes"}:
            print("Nothing was copied. You can add them later from the dashboard.")
            return 0

    summary = service.install_bundled([row["filename"] for row in missing])
    installed = summary["bundled_installed"]
    skipped = summary["bundled_skipped"]
    errors = summary["bundled_errors"]
    if installed:
        print("Copied into the dashboard library:")
        for row in installed:
            print(f"  - {row['filename']}")
    if skipped:
        print("Already in the library:")
        for name in skipped:
            print(f"  - {name}")
    if errors:
        print("Could not copy:", file=sys.stderr)
        for row in errors:
            print(f"  - {row['filename']}: {row['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
=======
#!/usr/bin/env python3
"""Copy models shipped in models/ into the dashboard model library.

Asks before copying unless --yes is passed. A model already in the library
is left unchanged.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from segment import ModelService  # noqa: E402
from segment_inspect import STORE_DIR  # noqa: E402


def format_size(num: int) -> str:
    if num >= 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    if num >= 1024:
        return f"{num / 1024:.1f} KB"
    return f"{num} B"


def library_ready() -> bool:
    return STORE_DIR.exists() or STORE_DIR.parent.exists()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Copy models shipped with this release into the dashboard library.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Copy without asking",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show shipped models that are not in the library yet",
    )
    args = parser.parse_args(argv)

    if not library_ready():
        print(
            f"The model library drive is not available ({STORE_DIR}).\n"
            "On a kit this is /mnt/recordings. Open the dashboard there and choose Add models,\n"
            "or run this script on the kit.",
            file=sys.stderr,
        )
        return 1

    service = ModelService(ROOT, store_dir=STORE_DIR)
    missing = [row for row in service.bundled_catalog() if not row["installed"]]
    if not missing:
        print("Every model shipped with this release is already in the dashboard library.")
        print(f"Library: {service.library_dir()}")
        return 0

    print("New models are available to use:")
    for row in missing:
        note = " — previously skipped on the dashboard" if row["dismissed"] else ""
        print(f"  - {row['filename']} ({format_size(int(row['bytes']))}){note}")
    print(f"Destination: {service.library_dir()}")
    print("Models already in the library are left as they are.")
    if args.list:
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            print("Nothing was copied. Run this script in a terminal and answer yes, or pass --yes.")
            return 0
        try:
            answer = input("Copy these models into the dashboard library? [y/N] ")
        except EOFError:
            print("Nothing was copied.")
            return 0
        if answer.strip().lower() not in {"y", "yes"}:
            print("Nothing was copied. You can add them later from the dashboard.")
            return 0

    summary = service.install_bundled([row["filename"] for row in missing])
    installed = summary["bundled_installed"]
    skipped = summary["bundled_skipped"]
    errors = summary["bundled_errors"]
    if installed:
        print("Copied into the dashboard library:")
        for row in installed:
            print(f"  - {row['filename']}")
    if skipped:
        print("Already in the library:")
        for name in skipped:
            print(f"  - {name}")
    if errors:
        print("Could not copy:", file=sys.stderr)
        for row in errors:
            print(f"  - {row['filename']}: {row['error']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7
