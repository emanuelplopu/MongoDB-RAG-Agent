"""Command-line entry point for quellex-profiler."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from quellex_profiler import __version__
from quellex_profiler.detect import collect_profile
from quellex_profiler.recommend import recommend_config
from quellex_profiler.schema import ProfilerReport


def build_report() -> ProfilerReport:
    profile = collect_profile()
    recommendation = recommend_config(profile)
    return ProfilerReport(
        profile=profile,
        recommendation=recommendation,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tool_version=__version__,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="quellex-profiler",
        description=(
            "Profile this machine and show the recommended Quellex configuration. "
            "Runs fully offline with no dependency on an existing Quellex install."
        ),
    )
    parser.add_argument("--json", action="store_true",
                        help="Print profile+recommendation as JSON and exit.")
    parser.add_argument("--html", metavar="PATH",
                        help="Write a single-file offline HTML report and exit.")
    parser.add_argument("--serve", action="store_true",
                        help="Serve the dashboard without auto-opening the browser.")
    parser.add_argument("--port", type=int, default=17645,
                        help="Local HTTP port to bind (default: 17645).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--version", action="version",
                        version=f"quellex-profiler {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = build_report()

    if args.json:
        from quellex_profiler.report.json_export import to_json_string

        sys.stdout.write(to_json_string(report) + "\n")
        return 0

    if args.html:
        from quellex_profiler.report.html_renderer import write_html

        out = write_html(report, args.html)
        print(f"[quellex-profiler] wrote offline report: {out}")
        return 0

    # Default and --serve both run the live dashboard.
    from quellex_profiler.server import serve

    serve(
        report=report,
        host=args.host,
        port=args.port,
        open_browser=not args.serve,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
