"""One run: fetch the dashboard, query the numbers, draw, push.

Called without arguments it does exactly that. ``--out file.png`` also
writes the image to disk, ``--no-push`` skips the push — both for working
at the desk without occupying the panel.

If a step fails, *nothing* is pushed. That is the quiet, correct choice:
the service keeps the last good image, the image hash does not change, the
panel does not refresh — so the wall shows an hour-old state instead of an
error message. The failure is in the job log.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import datetime, timezone

from . import draw as drawing
from .grafana import fetch_dashboard, run_queries
from .layout import place
from .panels import build_panels, snap_range, time_range_millis
from .push import push_png


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"environment variable {name} is missing")
    return value


def render_png(width: int, height: int, dashboard: dict,
               results: dict, now: datetime | None = None) -> bytes:
    """The pure part of a run: dashboard plus results become a PNG."""
    # The axis is snapped, the query was not — see panels.snap_range.
    t_from, t_to = snap_range(*time_range_millis(dashboard, now))
    panels = build_panels(dashboard, results)
    box = drawing.content_box(width, height)
    rects = place(panels, box)
    img = drawing.render(width, height, dashboard.get("title", ""),
                         panels, rects, t_from, t_to)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a Grafana dashboard onto the Inkplate")
    parser.add_argument("--out", help="also write the PNG to this file")
    parser.add_argument("--no-push", action="store_true",
                        help="do not send it to the eink service")
    args = parser.parse_args(argv)

    grafana_url = _env("GRAFANA_URL")
    grafana_token = _env("GRAFANA_TOKEN")
    uid = _env("GRAFANA_DASHBOARD_UID")
    width = int(os.environ.get("PANEL_WIDTH", "1200"))
    height = int(os.environ.get("PANEL_HEIGHT", "825"))

    now = datetime.now(tz=timezone.utc)
    dashboard = fetch_dashboard(grafana_url, grafana_token, uid)
    t_from, t_to = time_range_millis(dashboard, now)
    results = run_queries(grafana_url, grafana_token, dashboard, t_from, t_to)

    failed = [ref for ref, r in results.items() if r.get("status", 200) != 200]
    if failed:
        print(f"[warn] queries without a result: {', '.join(sorted(failed))}",
              file=sys.stderr)

    png = render_png(width, height, dashboard, results, now)
    print(f"[render] {dashboard.get('title','')!r} {width}×{height}, {len(png)} bytes")

    if args.out:
        with open(args.out, "wb") as fh:
            fh.write(png)
        print(f"[out] {args.out}")

    if args.no_push:
        return 0

    meta = push_png(_env("EINK_BASE_URL"), _env("EINK_PUSH_TOKEN"),
                    os.environ.get("EINK_CHANNEL", "inkplate10"), png)
    print(f"[push] hash={meta.get('last_modified')} size={meta.get('size')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
