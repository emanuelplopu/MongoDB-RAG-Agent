"""Render a single-file HTML report by inlining the bundled UI assets.

The offline HTML mirrors the live dashboard but embeds the JSON profile
directly into a ``window.__REPORT__`` global so it renders without the
local server.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from quellex_profiler.schema import ProfilerReport
from quellex_profiler.report.json_export import to_json_string


def _asset(name: str) -> str:
    return resources.files("quellex_profiler").joinpath("ui").joinpath(name).read_text(
        encoding="utf-8"
    )


def _asset_bytes(name: str) -> bytes:
    return resources.files("quellex_profiler").joinpath("ui").joinpath(name).read_bytes()


def render_html(report: ProfilerReport) -> str:
    html = _asset("index.html")
    css = _asset("styles.css")
    js = _asset("app.js")

    # Inline the logo as data URI so the single file is self-contained.
    import base64
    logo_b64 = base64.b64encode(_asset_bytes("quellex-logo.svg")).decode("ascii")
    logo_uri = f"data:image/svg+xml;base64,{logo_b64}"

    # Swap external asset references for inline content.
    html = html.replace(
        '<link rel="stylesheet" href="/static/styles.css" />',
        f"<style>{css}</style>",
    )
    html = html.replace('src="/static/quellex-logo.svg"', f'src="{logo_uri}"')

    # Replace the runtime fetch-based loader with an inline, already-loaded report.
    bootstrap = (
        "<script>window.__REPORT__ = "
        + to_json_string(report, indent=0)
        + ";</script>\n"
    )
    # Override fetch('/api/report') by providing a shim before the main script.
    shim = (
        "<script>"
        "window.fetch = function(url){"
        "  if(typeof url === 'string' && url.indexOf('/api/report') === 0){"
        "    return Promise.resolve({ok:true, status:200, json:function(){return Promise.resolve(window.__REPORT__);}});"
        "  }"
        "  return Promise.reject(new Error('offline report: no network'));"
        "};"
        "</script>\n"
    )
    html = html.replace(
        '<script src="/static/app.js"></script>',
        bootstrap + shim + f"<script>{js}</script>",
    )
    return html


def write_html(report: ProfilerReport, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(report), encoding="utf-8")
    return out
