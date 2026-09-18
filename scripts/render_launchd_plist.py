#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    template = repository / "config" / "com.host-ops.poll.plist.example"
    runtime_dir = (args.runtime_dir or repository).resolve()
    destination = args.destination or repository / "var" / "com.host-ops.poll.plist"
    content = template.read_text(encoding="utf-8").replace(
        "__HOST_OPS_REPOSITORY__", str(runtime_dir)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
