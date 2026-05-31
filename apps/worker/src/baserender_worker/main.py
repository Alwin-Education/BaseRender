from __future__ import annotations

import argparse
import json
import subprocess
import sys

from baserender.timeline_model import BaseRenderError
from baserender_worker.job import load_job, run_render_job


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute a BaseRender background render job.")
    parser.add_argument(
        "job",
        nargs="?",
        help="Path to a JSON render job payload. Reads one JSON object from stdin when omitted.",
    )
    parser.add_argument(
        "--output-report",
        help="Optional path where the render report JSON should be written.",
    )
    args = parser.parse_args()

    try:
        report = run_render_job(load_job(args.job))
    except subprocess.CalledProcessError as exc:
        print(f"FFmpeg failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode
    except (BaseRenderError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    report_json = json.dumps(report, indent=2, sort_keys=True)
    if args.output_report:
        with open(args.output_report, "w", encoding="utf-8") as handle:
            handle.write(report_json)
            handle.write("\n")
    print(report_json)
    return 0 if report.get("status") != "error" else 2


if __name__ == "__main__":
    raise SystemExit(main())
