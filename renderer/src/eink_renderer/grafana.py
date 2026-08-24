"""Query Grafana — the only place that talks to it.

Two calls per run: the dashboard JSON (title, panels, grid, time range)
and *one* combined query covering every panel. Grafana answers
``/api/ds/query`` with any number of queries at once, keyed by ``refId``.
That saves a connection per panel and returns all numbers from the same
instant instead of seven snapshots seconds apart.
"""

from __future__ import annotations

import requests

TIMEOUT = 60


class GrafanaError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def fetch_dashboard(base_url: str, token: str, uid: str) -> dict:
    """Fetch the dashboard JSON. Returns the ``dashboard`` block."""
    url = f"{base_url.rstrip('/')}/api/dashboards/uid/{uid}"
    r = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
    if r.status_code != 200:
        raise GrafanaError(f"dashboard {uid} not retrievable: HTTP {r.status_code}")
    body = r.json()
    if "dashboard" not in body:
        raise GrafanaError(f"response without a dashboard block for {uid}")
    return body["dashboard"]


def build_queries(dashboard: dict) -> list[dict]:
    """Build one query list from all panels, refId = ``P<panel-id>``."""
    queries: list[dict] = []
    for panel in dashboard.get("panels", []):
        if panel.get("type") == "row":
            continue
        targets = panel.get("targets") or []
        if not targets:
            continue
        target = dict(targets[0])
        ds = target.get("datasource") or panel.get("datasource")
        if not ds:
            continue
        target.update({
            "refId": f"P{panel.get('id')}",
            "datasource": ds,
            "rawQuery": True,
            "intervalMs": 86_400_000,
            "maxDataPoints": 2000,
        })
        # Only the first target per panel: several targets would produce
        # several frames under one refId, which the model handles — but
        # Grafana needs a unique refId per query. Panels here have one.
        queries.append(target)
    return queries


def run_queries(base_url: str, token: str, dashboard: dict,
                t_from: float, t_to: float) -> dict[str, dict]:
    """Run every panel query in a single call."""
    queries = build_queries(dashboard)
    if not queries:
        return {}
    url = f"{base_url.rstrip('/')}/api/ds/query"
    payload = {"queries": queries, "from": str(int(t_from)), "to": str(int(t_to))}
    r = requests.post(url, headers=_headers(token), json=payload, timeout=TIMEOUT)
    if r.status_code != 200:
        raise GrafanaError(f"query failed: HTTP {r.status_code} {r.text[:200]}")
    return r.json().get("results", {})
