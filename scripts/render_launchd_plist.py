#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


def main() -> None:
    repository = Path(__file__).resolve().parents[1]
    template = repository / "config" / "com.host-ops.poll.plist.example"
    destination = repository / "var" / "com.host-ops.poll.plist"
    content = template.read_text(encoding="utf-8").replace(
        "__HOST_OPS_REPOSITORY__", str(repository)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
