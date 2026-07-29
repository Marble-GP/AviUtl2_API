"""Discover AviUtl2 and print the Live Bridge handshake responses."""

from __future__ import annotations

import argparse
import json

from aviutl2_api.live import LiveClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, help="AviUtl2 process ID")
    args = parser.parse_args()

    with LiveClient.connect(pid=args.pid) as client:
        output = {
            "hello": client.hello(),
            "project": client.get_project_info(),
        }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
