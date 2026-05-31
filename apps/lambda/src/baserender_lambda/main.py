from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from baserender.timeline_model import BaseRenderError

from baserender_lambda.handler import lambda_handler


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Invoke the BaseRender Lambda FFmpeg handler locally."
    )
    parser.add_argument(
        "event",
        nargs="?",
        help="Path to a JSON Lambda event payload. Reads one JSON object from stdin when omitted.",
    )
    args = parser.parse_args()

    try:
        payload = _load_event(args.event)
        result = lambda_handler(payload)
    except (BaseRenderError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    result_json = json.dumps(result, indent=2, sort_keys=True)
    print(result_json)
    return 0 if result.get("status") != "error" else 2


def _load_event(path: str | None) -> dict[str, Any]:
    if path is None:
        payload = json.loads(sys.stdin.read())
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Lambda event payload must be a JSON object.")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
