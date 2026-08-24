# Grafana renderer

Draws a Grafana dashboard so it stays readable on an e-ink panel and
sends the image to the eink service. A CronJob runs it every 30 minutes;
the panel picks it up the next time it wakes.

## Why not just a screenshot

Grafana can render panels to PNG itself, but only with the
`grafana-image-renderer` plugin — a permanently running Chromium. The
monitoring Grafana here does not have it, and the output would be poor for
this target: eight gray levels turn thin coloured curves and 11-pixel axis
labels into a raster you cannot read from two metres away.

Instead this renderer fetches the same numbers Grafana displays
(`/api/ds/query`, one call for all panels) and draws them anew: large
figures, thick curves, series told apart by dash pattern rather than
colour. The grid (`gridPos`) comes from the dashboard, so a panel moved
there moves on the paper too.

What that costs: panel types not covered here appear as a labelled
placeholder instead of a chart. Supported are `timeseries`/`graph`/
`barchart` (as curves) and `stat`/`gauge` (as a large figure).

Display strings — numbers, dates, units — are German, because the
dashboard and its readers are.

## Running it locally

```bash
cd renderer
pdm install -G:all
cp .env.local.example .env.local     # fill in the tokens
pdm run render --no-push --out /tmp/dashboard.png
pdm run test
```

`--no-push` leaves the service alone, `--out` writes the PNG for
inspection. Without either, the run is exactly what the CronJob does.

## In the cluster

```bash
kubectl apply -f deploy/k8s/secret.yaml       # from secret.example.yaml, not in git
kubectl apply -f deploy/k8s/cronjob.yaml
kubectl -n eink create job --from=cronjob/eink-grafana-renderer probe   # run now
kubectl -n eink logs job/probe
```

The Grafana token belongs to a service account with the **Viewer** role.
That is all the renderer needs: read and query.

## Settings

| Variable | Required | Meaning |
|---|---|---|
| `GRAFANA_URL` | yes | base URL of the Grafana instance |
| `GRAFANA_TOKEN` | yes | service account token (Viewer) |
| `GRAFANA_DASHBOARD_UID` | yes | UID from the dashboard's address |
| `EINK_BASE_URL` | for the push | e.g. `http://eink:8989` inside the cluster |
| `EINK_PUSH_TOKEN` | for the push | the same one the service uses |
| `EINK_CHANNEL` | no | defaults to `inkplate10` |
| `PANEL_WIDTH` / `PANEL_HEIGHT` | no | default 1200 × 825 |

## Two things to know before changing it

**The image must stay byte-identical for identical numbers.** The service
compares hashes; a new hash costs the panel a real refresh (~2 s of
flashing, a slice of its lifetime). Two things follow.

**No time is drawn into the image** — the firmware draws it itself
whenever a new image arrives. A timestamp in the PNG would force a refresh
on every single run, even when no number changed.

**The time axis is snapped to whole days** (`panels.snap_range`). A
dashboard range of `now-30d` slides continuously, so without snapping
every data point drifts along the axis — measured at 0.69 px per hour on a
500 px plot, enough to change the image on every single run. The query
still uses the exact range; only the axis stands still.
`test_the_same_input_yields_the_same_bytes` and
`test_an_hour_later_yields_the_same_image_when_the_data_is_unchanged`
guard both.

**Two corners stay empty.** Bottom right the firmware draws the clock, top
right a battery symbol when the cell is weak (`drawClockOverlay`,
`drawPowerStatusOverlay` in `firmware/src/main.cpp`). Both paint a white
box over whatever is underneath. The measurements are `CLOCK_ZONE` and
`BATTERY_ZONE` in `draw.py` and two tests check them.
