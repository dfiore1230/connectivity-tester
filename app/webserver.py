#!/usr/bin/env python3
import os
import json
from pathlib import Path
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    from mqtt_publisher import MqttPublisher, build_settings_from_env
except Exception:
    MqttPublisher = None  # type: ignore
    build_settings_from_env = None  # type: ignore

LOG_FILE = "/logs/connectivity.log"
CONFIG_FILE = "/logs/config.env"
MAX_RECORDS = 500
WEB_PORT = int(os.environ.get("WEB_PORT", "8080"))
STATIC_ROOT = Path(__file__).parent / "static"

ENV_TARGETS = os.environ.get("TARGETS", "")
ENV_TARGET_HOST = os.environ.get("TARGET_HOST", "8.8.8.8")
ENV_INTERVAL = os.environ.get("INTERVAL_SECONDS", "30")


SUMMARY_CACHE = {
    "daily_state": {},
    "summary": [],
    "position": 0,
    "inode": None,
    "size": 0,
    "build_ts": None,
}


def reset_summary_cache():
    SUMMARY_CACHE.update(
        {
            "daily_state": {},
            "summary": [],
            "position": 0,
            "inode": None,
            "size": 0,
            "build_ts": None,
        }
    )


def _ensure_day_state(daily: dict, day: str):
    if day not in daily:
        daily[day] = {
            "date": day,
            "total_probes": 0,
            "total_sent": 0,
            "total_received": 0,
            "loss_sum": 0.0,
            "loss_count": 0,
            "good_probes": 0,
            "degraded_probes": 0,
            "down_probes": 0,
            "rtt_sum": 0.0,
            "rtt_count": 0,
            "rtt_min": None,
            "rtt_max": None,
            "targets": set(),
            "public_ips": set(),
        }
    return daily[day]


def _summaries_from_state(daily: dict):
    result = []
    for day, d in daily.items():
        if d["total_probes"] > 0:
            uptime_pct = (
                100.0 * (d["total_probes"] - d["down_probes"]) / d["total_probes"]
            )
        else:
            uptime_pct = 0.0
        if d["loss_count"] > 0:
            avg_loss = d["loss_sum"] / d["loss_count"]
        else:
            avg_loss = 0.0
        if d["rtt_count"] > 0:
            avg_rtt = d["rtt_sum"] / d["rtt_count"]
        else:
            avg_rtt = None

        result.append(
            {
                "date": day,
                "total_probes": d["total_probes"],
                "uptime_pct": uptime_pct,
                "avg_loss_pct": avg_loss,
                "avg_rtt_ms": avg_rtt,
                "min_rtt_ms": d["rtt_min"],
                "max_rtt_ms": d["rtt_max"],
                "good_probes": d["good_probes"],
                "degraded_probes": d["degraded_probes"],
                "down_probes": d["down_probes"],
                "targets": sorted(d["targets"]),
                "public_ips": sorted(d["public_ips"]),
            }
        )

    result.sort(key=lambda x: x["date"])
    return result


def read_recent_records():
    """Last MAX_RECORDS records for charts & raw table."""
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
    except Exception:
        return []

    records = []
    for line in lines[-MAX_RECORDS:]:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            records.append(rec)
        except json.JSONDecodeError:
            continue
    return records


def read_records_for_day(day_str: str):
    """All records for a specific YYYY-MM-DD."""
    if not os.path.exists(LOG_FILE) or not day_str:
        return []

    records = []
    with open(LOG_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("timestamp") or ""
            if not ts.startswith(day_str):
                continue
            records.append(rec)

    records.sort(key=lambda r: r.get("timestamp", ""))
    return records


def build_daily_summary_from_file():
    """
    Build per-day summaries with a cache that reuses the last build
    until the log file grows or changes.
    """
    if not os.path.exists(LOG_FILE):
        reset_summary_cache()
        return []

    try:
        stat = os.stat(LOG_FILE)
    except FileNotFoundError:
        reset_summary_cache()
        return []

    cache = SUMMARY_CACHE
    if cache["inode"] != stat.st_ino or stat.st_size < cache["size"]:
        reset_summary_cache()
        cache = SUMMARY_CACHE

    if (
        cache["inode"] == stat.st_ino
        and cache["position"] == stat.st_size
        and cache["summary"]
    ):
        return cache["summary"]

    daily = cache["daily_state"]
    start_pos = cache["position"] or 0

    with open(LOG_FILE, "r") as f:
        if start_pos:
            f.seek(start_pos)

        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = rec.get("timestamp")
            if not ts:
                continue
            day = ts.split("T")[0]
            d = _ensure_day_state(daily, day)

            d["total_probes"] += 1

            sent = rec.get("sent")
            recv = rec.get("received")
            if isinstance(sent, (int, float)):
                d["total_sent"] += int(sent)
            if isinstance(recv, (int, float)):
                d["total_received"] += int(recv)

            loss = rec.get("loss_pct")
            if isinstance(loss, (int, float)):
                d["loss_sum"] += float(loss)
                d["loss_count"] += 1
                if loss == 0:
                    d["good_probes"] += 1
                elif loss == 100:
                    d["down_probes"] += 1
                else:
                    d["degraded_probes"] += 1

            rtt = rec.get("rtt_avg_ms")
            try:
                rtt_val = float(rtt)
            except (TypeError, ValueError):
                rtt_val = None
            if rtt_val is not None:
                d["rtt_sum"] += rtt_val
                d["rtt_count"] += 1
                if d["rtt_min"] is None or rtt_val < d["rtt_min"]:
                    d["rtt_min"] = rtt_val
                if d["rtt_max"] is None or rtt_val > d["rtt_max"]:
                    d["rtt_max"] = rtt_val

            tgt = rec.get("target") or rec.get("dst_host")
            if tgt:
                d["targets"].add(str(tgt))
            pub = rec.get("public_ip")
            if pub:
                d["public_ips"].add(str(pub))

        cache["position"] = f.tell()

    cache["size"] = stat.st_size
    cache["inode"] = stat.st_ino
    cache["build_ts"] = time.time()
    cache["summary"] = _summaries_from_state(daily)
    return cache["summary"]


def read_config():
    """
    Read config.env if present and merge with env defaults.
    Returns (targets_display, interval_str).
    """
    targets = ENV_TARGETS
    interval = ENV_INTERVAL

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k == "TARGETS":
                        targets = v
                    elif k == "INTERVAL_SECONDS":
                        interval = v
        except Exception:
            pass

    if not targets:
        targets_display = ENV_TARGETS or ENV_TARGET_HOST
    else:
        targets_display = targets

    if not interval:
        interval = ENV_INTERVAL or "30"

    return targets_display, interval


class Handler(BaseHTTPRequestHandler):
    def _send_file(self, file_path: Path):
        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return

        mime = "text/plain"
        if file_path.suffix == ".js":
            mime = "text/javascript"
        elif file_path.suffix == ".css":
            mime = "text/css"

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, data, status=200):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, html, status=200):
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            safe_path = (STATIC_ROOT / rel).resolve()
            if not str(safe_path).startswith(str(STATIC_ROOT.resolve())):
                self.send_error(404)
                return
            self._send_file(safe_path)
            return

        if path == "/data":
            records = read_recent_records()
            self._send_json(records)
            return

        if path == "/daily":
            summary = build_daily_summary_from_file()
            self._send_json(summary)
            return

        if path == "/day":
            qs = parse_qs(parsed.query)
            day = qs.get("date", [""])[0]
            records = read_records_for_day(day)
            self._send_html(self._render_day_page(day, records))
            return

        # Main dashboard
        targets_display, interval_str = read_config()
        self._send_html(self._render_main_page(targets_display, interval_str))

    def _render_main_page(self, targets_display, interval_str):
        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Connectivity Monitor</title>
  <style>
    :root {{
      --card-radius: 8px;
      --card-border: #ddd;
      --card-shadow: 0 1px 3px rgba(0,0,0,0.04);
      --gap: 16px;
      --bg-body: #f5f5f7;
      --bg-card: #ffffff;
      --border-color: #e5e7eb;
      --text-main: #111827;
      --text-muted: #6b7280;
      --text-soft: #9ca3af;
      --table-stripe: #f9fafb;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg-body: #111827;
        --bg-card: #1f2937;
        --border-color: #374151;
        --text-main: #e5e7eb;
        --text-muted: #9ca3af;
        --text-soft: #6b7280;
        --table-stripe: #111827;
        --card-shadow: 0 1px 4px rgba(0,0,0,0.5);
      }}
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      padding: 0;
      background: var(--bg-body);
      color: var(--text-main);
    }}

    .dashboard {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 16px 16px 32px 16px;
      display: grid;
      grid-template-rows: auto auto auto auto;
      gap: var(--gap);
    }}

    header {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    header h1 {{
      font-size: 22px;
      margin: 0;
    }}

    header .meta {{
      color: var(--text-muted);
      font-size: 13px;
    }}

    .meta-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px 16px;
      font-size: 12px;
      color: var(--text-soft);
      align-items: baseline;
    }}

    .meta-row span {{
      white-space: nowrap;
    }}

    #status {{
      font-size: 13px;
      font-weight: 600;
    }}
    #status.good {{ color: #22c55e; }}
    #status.bad {{ color: #f97373; }}
    #status.degraded {{ color: #fbbf24; }}
    #status.small {{ color: var(--text-muted); font-weight: 400; }}

    .layout-top {{
      display: grid;
      gap: var(--gap);
    }}

    @media (min-width: 900px) {{
      .layout-top {{
        grid-template-columns: 2fr 1fr;
        align-items: start;
      }}
    }}

    .charts {{
      display: grid;
      grid-template-columns: 1fr;
      gap: var(--gap);
    }}

    @media (min-width: 700px) {{
      .charts {{
        grid-template-columns: 1fr 1fr;
      }}
    }}

    .card {{
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--card-radius);
      box-shadow: var(--card-shadow);
      padding: 10px 12px;
    }}

    .card h2 {{
      font-size: 16px;
      margin: 0 0 4px 0;
    }}

    .subtitle {{
      font-size: 12px;
      color: var(--text-muted);
      margin-bottom: 8px;
    }}

    .toolbar {{
      display: flex;
      justify-content: flex-end;
      margin-top: 4px;
      margin-bottom: 4px;
      gap: 8px;
    }}

    .toolbar button {{
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 4px;
      border: 1px solid var(--border-color);
      background: transparent;
      color: var(--text-main);
      cursor: pointer;
    }}

    .toolbar button:hover {{
      background: rgba(148,163,184,0.2);
    }}

    .chart-card {{
      display: flex;
      flex-direction: column;
      height: 33vh;
      min-height: 200px;
      max-height: 300px;
    }}

    .chart-container {{
      flex: 1 1 auto;
      position: relative;
    }}

    .chart-container canvas {{
      width: 100% !important;
      height: 100% !important;
      display: block;
    }}

    .info-card {{
      font-size: 13px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .info-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px 12px;
      margin-top: 4px;
    }}

    .info-label {{
      color: var(--text-muted);
    }}
    .info-value {{
      font-weight: 500;
      word-break: break-all;
    }}

    .settings {{
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px solid var(--border-color);
      font-size: 12px;
    }}

    .settings-row {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      margin-bottom: 6px;
    }}

    .settings-row label {{
      color: var(--text-muted);
      font-size: 12px;
    }}

    .settings-row input {{
      font-size: 12px;
      padding: 4px 6px;
      border-radius: 4px;
      border: 1px solid var(--border-color);
      background: transparent;
      color: var(--text-main);
    }}

    .settings-actions {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 4px;
    }}

    .settings-actions button {{
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 4px;
      border: 1px solid var(--border-color);
      background: transparent;
      color: var(--text-main);
      cursor: pointer;
    }}

    .settings-actions button:hover {{
      background: rgba(148,163,184,0.2);
    }}

    .table-card {{
      font-size: 13px;
    }}

    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}

    th, td {{
      border: 1px solid var(--border-color);
      padding: 4px 6px;
      text-align: left;
      white-space: nowrap;
    }}

    th {{
      background: #e5e7eb;
      position: sticky;
      top: 0;
      z-index: 1;
      cursor: pointer;
    }}

    @media (prefers-color-scheme: dark) {{
      th {{
        background: #374151;
      }}
    }}

    tbody tr:nth-child(even) {{
      background: var(--table-stripe);
    }}

    tr.good {{ background-color: #0f172a11; }}
    tr.bad {{ background-color: #7f1d1d22; }}
    tr.degraded {{ background-color: #92400e22; }}

    .table-wrapper {{
      max-height: 45vh;
      overflow: auto;
      margin-top: 8px;
      border-radius: 6px;
      border: 1px solid var(--border-color);
    }}

    .small-text {{
      font-size: 11px;
      color: var(--text-soft);
      margin-top: 4px;
    }}

    a.day-link {{
      color: inherit;
      text-decoration: underline;
      text-decoration-style: dotted;
    }}
  </style>
  <script src="/static/js/helpers.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-zoom@2.0.1/dist/chartjs-plugin-zoom.umd.min.js"></script>
</head>
<body>
  <div class="dashboard">
    <header>
      <h1>Connectivity Monitor</h1>
      <div class="meta">
        Visualizes RTT, loss, and IP info for one or more targets.
      </div>
      <div class="meta-row">
        <span><strong>Targets:</strong> <span id="meta-targets">{targets_display}</span></span>
        <span><strong>Probe interval:</strong> ~<span id="meta-interval">{interval_str}</span>s</span>
        <span><strong>Last update:</strong> <span id="last-update">never</span></span>
      </div>
      <div id="status" class="small">Loading...</div>
    </header>

    <div class="layout-top">
      <div>
        <div class="toolbar">
          <button id="resetZoom" type="button">Reset Zoom</button>
        </div>
        <div class="charts">
          <div class="card chart-card">
            <h2>Average RTT</h2>
            <div class="subtitle">Per probe, in milliseconds (multi-series if multiple targets)</div>
            <div class="chart-container">
              <canvas id="rttChart"></canvas>
            </div>
          </div>

          <div class="card chart-card">
            <h2>Uptime per Probe</h2>
            <div class="subtitle">100 - packet loss (%) for each probe</div>
            <div class="chart-container">
              <canvas id="uptimeChart"></canvas>
            </div>
          </div>
        </div>
      </div>

      <div class="card info-card">
        <h2>Current Snapshot</h2>
        <div class="subtitle">Based on the latest probe (last target logged)</div>
        <div class="info-grid">
          <div class="info-label">Target</div>
          <div class="info-value" id="info-target">-</div>

          <div class="info-label">Dst Host/IP</div>
          <div class="info-value" id="info-dst">-</div>

          <div class="info-label">Src IP</div>
          <div class="info-value" id="info-src">-</div>

          <div class="info-label">Public IP</div>
          <div class="info-value" id="info-public">-</div>

          <div class="info-label">Last RTT (ms)</div>
          <div class="info-value" id="info-rtt">-</div>

          <div class="info-label">Last Loss (%)</div>
          <div class="info-value" id="info-loss">-</div>

          <div class="info-label">Samples</div>
          <div class="info-value" id="info-samples">-</div>
        </div>

        <div class="settings">
          <h3 style="margin:0 0 6px 0;font-size:13px;">Settings</h3>
          <div class="settings-row">
            <label for="cfg-targets">Targets (comma-separated, e.g. <code>GoogleDNS=8.8.8.8,Cloudflare=1.1.1.1</code>)</label>
            <input id="cfg-targets" type="text" value="{targets_display}">
          </div>
          <div class="settings-row">
            <label for="cfg-interval">Probe interval (seconds)</label>
            <input id="cfg-interval" type="number" min="1" value="{interval_str}">
          </div>
          <div class="settings-actions">
            <button type="button" id="save-config">Save</button>
          </div>
          <div class="small-text" id="settings-status">Changes apply on the next probe cycle.</div>
        </div>
      </div>
    </div>

    <div class="card table-card">
      <h2>Raw Probe Log</h2>
      <div class="subtitle">Most recent {MAX_RECORDS} entries (sorted; click headers to change sort).</div>
      <div class="table-wrapper">
        <table id="log-table">
          <thead>
            <tr>
              <th data-col="0">Timestamp</th>
              <th data-col="1">Target</th>
              <th data-col="2">Src IP</th>
              <th data-col="3">Public IP</th>
              <th data-col="4">Dst Host</th>
              <th data-col="5">Dst IP</th>
              <th data-col="6">Sent</th>
              <th data-col="7">Recv</th>
              <th data-col="8">Loss %</th>
              <th data-col="9">RTT Avg (ms)</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="small-text">
        Exportable via the connectivity.log file on disk for sending to your ISP.
      </div>
    </div>

    <div class="card table-card">
      <h2>Daily Summary</h2>
      <div class="subtitle">
        One row per day across the entire log (indefinite history as long as the log is retained).
        Click a date to open full details for that day in a new tab.
      </div>
      <div class="settings-actions" style="margin-top:4px;margin-bottom:4px;">
        <button type="button" id="rebuild-daily">Rebuild summaries</button>
        <button type="button" id="export-daily">Export CSV</button>
      </div>
      <div class="small-text" id="daily-status"></div>
      <div class="table-wrapper" style="max-height:35vh;">
        <table id="daily-table">
          <thead>
            <tr>
              <th data-dcol="0">Date</th>
              <th data-dcol="1">Probes</th>
              <th data-dcol="2">Uptime %</th>
              <th data-dcol="3">Avg Loss %</th>
              <th data-dcol="4">Avg RTT (ms)</th>
              <th data-dcol="5">Min RTT</th>
              <th data-dcol="6">Max RTT</th>
              <th data-dcol="7">Down Probes</th>
              <th data-dcol="8">Targets</th>
              <th data-dcol="9">Public IPs</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="small-text">
        Use this as a point-in-time record for each day (e.g., when showing your provider long-term behavior).
      </div>
    </div>
  </div>

  <script>
    let rttChart = null;
    let uptimeChart = null;
    let lastRows = [];
    let dailySummary = [];
    const sortState = {{ index: 0, dir: 'asc' }};        // raw table
    const numericCols = new Set([6,7,8,9]);              // raw table numeric

    const dailySortState = {{ index: 0, dir: 'desc' }};  // daily summary (default newest date first)
    const dailyNumericCols = new Set([1,2,3,4,5,6,7]);   // daily numeric cols
    const helpers = window.ConnectivityHelpers;

    async function fetchData() {{
      try {{
        const res = await fetch('/data');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        lastRows = data || [];
        renderTable(lastRows);
        renderCharts(lastRows);
        updateInfoPanel(lastRows);
        const lu = document.getElementById('last-update');
        if (lu) lu.textContent = new Date().toLocaleTimeString();
      }} catch (e) {{
        const status = document.getElementById('status');
        status.textContent = 'Error loading data: ' + e;
        status.className = 'bad';
      }}
    }}

    async function fetchDaily() {{
      try {{
        const res = await fetch('/daily');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        dailySummary = data || [];
        renderDailyTable(dailySummary);
      }} catch (e) {{
        // ignore daily summary errors
      }}
    }}

    async function rebuildSummaries() {{
      const btn = document.getElementById('rebuild-daily');
      const status = document.getElementById('daily-status');
      if (btn) btn.disabled = true;
      if (status) status.textContent = 'Rebuilding...';

      try {{
        const res = await fetch('/rebuild-summaries', {{ method: 'POST' }});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        if (status) status.textContent = data.message || 'Summary cache cleared.';
        await fetchDaily();
      }} catch (e) {{
        if (status) status.textContent = 'Error rebuilding: ' + e;
      }} finally {{
        if (btn) btn.disabled = false;
      }}
    }}

    function normalizeRows(rows) {{
      if (!rows || rows.length === 0) return [];
      rows.sort((a, b) => {{
        if (a.timestamp < b.timestamp) return -1;
        if (a.timestamp > b.timestamp) return 1;
        return 0;
      }});
      return rows;
    }}

    function sortedRows(rows) {{
      const mapper = (r) => [
        r.timestamp || '',
        r.target || r.dst_host || '',
        r.src_ip || '',
        r.public_ip || '',
        r.dst_host || '',
        r.dst_ip || '',
        r.sent,
        r.received,
        r.loss_pct,
        r.rtt_avg_ms
      ];
      return helpers.sortData(rows, sortState, mapper, numericCols);
    }}

    function sortedDailyRows(rows) {{
      const mapper = (r) => [
        r.date,
        r.total_probes,
        r.uptime_pct,
        r.avg_loss_pct,
        r.avg_rtt_ms,
        r.min_rtt_ms,
        r.max_rtt_ms,
        r.down_probes,
        (r.targets || []).join(', '),
        (r.public_ips || []).join(', ')
      ];
      return helpers.sortData(rows, dailySortState, mapper, dailyNumericCols);
    }}

    function buildChartData(rows) {{
      const norm = helpers.normalizeRows([...rows]);
      if (norm.length === 0) return {{ labels: [], fullLabels: [], datasetsRtt: [], datasetsUp: [] }};

      const fullLabels = norm.map(r => r.timestamp || '');
      const labels = fullLabels.map(ts => {{
        const t = (ts || '').split('T')[1] || ts;
        return t.substring(0, 5); // HH:MM
      }});

      const targetMap = {{}};
      const targetKeys = [];
      norm.forEach(r => {{
        const key = r.target || r.dst_host || 'default';
        if (!targetMap[key]) {{
          targetMap[key] = true;
          targetKeys.push(key);
        }}
      }});

      const colors = ['#3b82f6', '#22c55e', '#f97316', '#e11d48', '#8b5cf6', '#14b8a6'];
      const datasetsRtt = [];
      const datasetsUp = [];

      targetKeys.forEach((key, idx) => {{
        const dataRtt = new Array(norm.length).fill(null);
        const dataUp = new Array(norm.length).fill(null);

        norm.forEach((r, i) => {{
          const rowKey = r.target || r.dst_host || 'default';
          if (rowKey !== key) return;

          const rtt = Number(r.rtt_avg_ms);
          if (!Number.isNaN(rtt)) {{
            dataRtt[i] = rtt;
          }}

          const loss = Number(r.loss_pct || 0);
          let up = 100 - loss;
          if (up < 0) up = 0;
          if (up > 100) up = 100;
          dataUp[i] = up;
        }});

        const color = colors[idx % colors.length];

        datasetsRtt.push({{
          label: key,
          data: dataRtt,
          spanGaps: true,
          fill: false,
          borderColor: color,
          backgroundColor: color,
          tension: 0.1
        }});

        datasetsUp.push({{
          label: key,
          data: dataUp,
          spanGaps: true,
          fill: false,
          borderColor: color,
          backgroundColor: color,
          tension: 0.1,
          stepped: true
        }});
      }});

      return {{ labels, fullLabels, datasetsRtt, datasetsUp }};
    }}

    function renderTable(rows) {{
      const tbody = document.querySelector('#log-table tbody');
      tbody.innerHTML = '';

      if (!rows || rows.length === 0) {{
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 10;
        td.textContent = 'No data yet.';
        tr.appendChild(td);
        tbody.appendChild(tr);

        const status = document.getElementById('status');
        status.textContent = 'Waiting for first measurements...';
        status.className = 'small';
        return;
      }}

      const norm = sortedRows(rows);
      const latest = helpers.normalizeRows([...rows])[rows.length - 1];
      const loss = Number(latest.loss_pct || 0);
      const status = document.getElementById('status');

      if (loss === 0) {{
        status.textContent = 'Latest probe: OK (' + (latest.target || latest.dst_host) +
          ' ' + (latest.dst_ip || '') + ', ' + (latest.rtt_avg_ms || 'n/a') + ' ms avg)';
        status.className = 'good';
      }} else if (loss === 100) {{
        status.textContent = 'Latest probe: DOWN (100% packet loss to ' + (latest.target || latest.dst_host) + ')';
        status.className = 'bad';
      }} else {{
        status.textContent = 'Latest probe: DEGRADED (' + loss + '% packet loss)';
        status.className = 'degraded';
      }}

      norm.forEach(r => {{
        const tr = document.createElement('tr');
        const lossVal = Number(r.loss_pct || 0);
        if (lossVal === 0) tr.className = 'good';
        else if (lossVal === 100) tr.className = 'bad';
        else tr.className = 'degraded';

        const cells = [
          r.timestamp || '',
          r.target || r.dst_host || '',
          r.src_ip || '',
          r.public_ip || '',
          r.dst_host || '',
          r.dst_ip || '',
          r.sent != null ? r.sent : '',
          r.received != null ? r.received : '',
          r.loss_pct != null ? r.loss_pct : '',
          r.rtt_avg_ms != null ? r.rtt_avg_ms : ''
        ];

        cells.forEach(val => {{
          const td = document.createElement('td');
          td.textContent = val;
          tr.appendChild(td);
        }});

        tbody.appendChild(tr);
      }});
    }}

    function renderDailyTable(rows) {{
      const tbody = document.querySelector('#daily-table tbody');
      tbody.innerHTML = '';

      if (!rows || rows.length === 0) {{
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 10;
        td.textContent = 'No daily data yet.';
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
      }}

      const sorted = sortedDailyRows(rows);

      sorted.forEach(d => {{
        const tr = document.createElement('tr');

        function fmt(n, digits=2) {{
          if (n === null || n === undefined || isNaN(n)) return '';
          return Number(n).toFixed(digits);
        }}

        const dateCell = document.createElement('td');
        const link = document.createElement('a');
        link.href = '/day?date=' + encodeURIComponent(d.date);
        link.textContent = d.date;
        link.className = 'day-link';
        link.target = '_blank';
        dateCell.appendChild(link);

        const cells = [
          dateCell,
          d.total_probes,
          fmt(d.uptime_pct, 2),
          fmt(d.avg_loss_pct, 2),
          fmt(d.avg_rtt_ms, 2),
          fmt(d.min_rtt_ms, 2),
          fmt(d.max_rtt_ms, 2),
          d.down_probes,
          (d.targets || []).join(', '),
          (d.public_ips || []).join(', ')
        ];

        cells.forEach((val, idx) => {{
          if (idx === 0) {{
            tr.appendChild(val);
          }} else {{
            const td = document.createElement('td');
            td.textContent = val;
            tr.appendChild(td);
          }}
        }});

        tbody.appendChild(tr);
      }});
    }}

    function renderCharts(rows) {{
      const chartData = buildChartData(rows);
      const labels = chartData.labels;
      const fullLabels = chartData.fullLabels;

      const rttCtx = document.getElementById('rttChart').getContext('2d');
      const uptimeCtx = document.getElementById('uptimeChart').getContext('2d');

      if (!rttChart) {{
        rttChart = new Chart(rttCtx, {{
          type: 'line',
          data: {{
            labels: labels,
            datasets: chartData.datasetsRtt
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {{
              x: {{
                title: {{ display: true, text: 'Time' }},
                ticks: {{
                  autoSkip: true,
                  maxTicksLimit: 6,
                  callback: (val) => labels[val] || ''
                }}
              }},
              y: {{
                title: {{ display: true, text: 'RTT (ms)' }},
                beginAtZero: true,
                ticks: {{
                  maxTicksLimit: 5
                }}
              }}
            }},
            plugins: {{
              legend: {{ display: true, position: 'bottom' }},
              tooltip: {{
                callbacks: {{
                  title: (items) => {{
                    const idx = items[0].dataIndex;
                    const chart = items[0].chart;
                    const full = chart._fullLabels && chart._fullLabels[idx];
                    return full || items[0].label;
                  }}
                }}
              }},
              zoom: {{
                zoom: {{
                  wheel: {{ enabled: true }},
                  pinch: {{ enabled: true }},
                  mode: 'x'
                }},
                pan: {{
                  enabled: true,
                  mode: 'x'
                }}
              }}
            }}
          }}
        }});
      }} else {{
        rttChart.data.labels = labels;
        rttChart.data.datasets = chartData.datasetsRtt;
        rttChart.update();
      }}
      rttChart._fullLabels = fullLabels;

      if (!uptimeChart) {{
        uptimeChart = new Chart(uptimeCtx, {{
          type: 'line',
          data: {{
            labels: labels,
            datasets: chartData.datasetsUp
          }},
          options: {{
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            scales: {{
              x: {{
                title: {{ display: true, text: 'Time' }},
                ticks: {{
                  autoSkip: true,
                  maxTicksLimit: 6,
                  callback: (val) => labels[val] || ''
                }}
              }},
              y: {{
                title: {{ display: true, text: 'Uptime (%)' }},
                beginAtZero: true,
                suggestedMax: 100,
                ticks: {{
                  callback: value => value + '%',
                  stepSize: 50,
                  maxTicksLimit: 3
                }}
              }}
            }},
            plugins: {{
              legend: {{ display: true, position: 'bottom' }},
              tooltip: {{
                callbacks: {{
                  title: (items) => {{
                    const idx = items[0].dataIndex;
                    const chart = items[0].chart;
                    const full = chart._fullLabels && chart._fullLabels[idx];
                    return full || items[0].label;
                  }}
                }}
              }},
              zoom: {{
                zoom: {{
                  wheel: {{ enabled: true }},
                  pinch: {{ enabled: true }},
                  mode: 'x'
                }},
                pan: {{
                  enabled: true,
                  mode: 'x'
                }}
              }}
            }}
          }}
        }});
      }} else {{
        uptimeChart.data.labels = labels;
        uptimeChart.data.datasets = chartData.datasetsUp;
        uptimeChart.update();
      }}
      uptimeChart._fullLabels = fullLabels;
    }}

      function updateInfoPanel(rows) {{
        if (!rows || rows.length === 0) return;
        const norm = helpers.normalizeRows([...rows]);
        const latest = norm[norm.length - 1];

      document.getElementById('info-target').textContent =
        latest.target || latest.dst_host || '-';
      document.getElementById('info-dst').textContent =
        (latest.dst_host || '-') + (latest.dst_ip ? ' (' + latest.dst_ip + ')' : '');
      document.getElementById('info-src').textContent = latest.src_ip || '-';
      document.getElementById('info-public').textContent = latest.public_ip || '-';
      document.getElementById('info-rtt').textContent =
        latest.rtt_avg_ms != null ? latest.rtt_avg_ms : '-';
      document.getElementById('info-loss').textContent =
        latest.loss_pct != null ? latest.loss_pct : '-';
      document.getElementById('info-samples').textContent = norm.length.toString();
    }}

    function downloadDailyCsv() {{
      if (!dailySummary || dailySummary.length === 0) return;

      const csv = helpers.buildDailyCsv(dailySummary);
      const blob = new Blob([csv], {{ type: 'text/csv' }});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'daily-summary.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }}

    document.getElementById('resetZoom').addEventListener('click', () => {{
      if (rttChart && rttChart.resetZoom) rttChart.resetZoom();
      if (uptimeChart && uptimeChart.resetZoom) uptimeChart.resetZoom();
    }});

    // Sort handlers for raw table
    document.querySelectorAll('#log-table thead th').forEach(th => {{
      th.addEventListener('click', () => {{
        const idx = Number(th.getAttribute('data-col'));
        if (sortState.index === idx) {{
          sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
        }} else {{
          sortState.index = idx;
          sortState.dir = 'asc';
        }}
        renderTable(lastRows);
      }});
    }});

    // Sort handlers for daily summary table
    document.querySelectorAll('#daily-table thead th').forEach(th => {{
      th.addEventListener('click', () => {{
        const idx = Number(th.getAttribute('data-dcol'));
        if (dailySortState.index === idx) {{
          dailySortState.dir = dailySortState.dir === 'asc' ? 'desc' : 'asc';
        }} else {{
          dailySortState.index = idx;
          dailySortState.dir = 'asc';
        }}
        renderDailyTable(dailySummary);
      }});
    }});

    // Save config from UI
    document.getElementById('save-config').addEventListener('click', async () => {{
      const targets = document.getElementById('cfg-targets').value.trim();
      const interval = document.getElementById('cfg-interval').value.trim();
      const statusEl = document.getElementById('settings-status');

      try {{
        const res = await fetch('/config', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ targets, interval_seconds: interval }})
        }});
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        document.getElementById('meta-targets').textContent = data.targets_display;
        document.getElementById('meta-interval').textContent = data.interval_seconds;
        statusEl.textContent = 'Saved. Changes apply on the next probe cycle.';
      }} catch (e) {{
        statusEl.textContent = 'Error saving config: ' + e;
      }}
    }});

    document.getElementById('rebuild-daily').addEventListener('click', rebuildSummaries);
    document.getElementById('export-daily').addEventListener('click', downloadDailyCsv);

    fetchData();
    fetchDaily();
    setInterval(fetchData, 5000);     // short-term charts / raw log
    setInterval(fetchDaily, 60000);   // daily summary changes slowly
  </script>
</body>
</html>
"""

    def _render_day_page(self, day: str, records):
        # Day detail view: sortable table + CSV export for this single day
        records = sorted(records, key=lambda r: r.get("timestamp", ""))
        rows_html = ""

        if not records:
            rows_html = "<tr><td colspan='10'>No records found for this date.</td></tr>"
        else:
            for r in records:
                ts = r.get("timestamp", "")
                tgt = r.get("target") or r.get("dst_host") or ""
                src = r.get("src_ip") or ""
                pub = r.get("public_ip") or ""
                dsth = r.get("dst_host") or ""
                dstip = r.get("dst_ip") or ""
                sent = r.get("sent", "")
                recv = r.get("received", "")
                loss = r.get("loss_pct", "")
                rtt = r.get("rtt_avg_ms", "")
                rows_html += f"""
<tr>
  <td>{ts}</td>
  <td>{tgt}</td>
  <td>{src}</td>
  <td>{pub}</td>
  <td>{dsth}</td>
  <td>{dstip}</td>
  <td>{sent}</td>
  <td>{recv}</td>
  <td>{loss}</td>
  <td>{rtt}</td>
</tr>
"""

        return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Connectivity Detail - {day}</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      padding: 16px;
      background: #111827;
      color: #e5e7eb;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    a {{
      color: #60a5fa;
    }}
    .card {{
      background: #1f2937;
      border: 1px solid #374151;
      border-radius: 8px;
      padding: 12px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.5);
    }}
    h1 {{
      font-size: 20px;
      margin-top: 0;
    }}
    .toolbar {{
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin: 4px 0 8px 0;
    }}
    .toolbar button {{
      font-size: 12px;
      padding: 4px 8px;
      border-radius: 4px;
      border: 1px solid #4b5563;
      background: transparent;
      color: #e5e7eb;
      cursor: pointer;
    }}
    .toolbar button:hover {{
      background: #374151;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}
    th, td {{
      border: 1px solid #374151;
      padding: 4px 6px;
      white-space: nowrap;
      text-align: left;
    }}
    th {{
      background: #374151;
      position: sticky;
      top: 0;
      z-index: 1;
      cursor: pointer;
    }}
    tbody tr:nth-child(even) {{
      background: #111827;
    }}
    .table-wrapper {{
      max-height: 80vh;
      overflow: auto;
      margin-top: 8px;
      border-radius: 6px;
      border: 1px solid #374151;
    }}
  .small-text {{
      font-size: 11px;
      color: #9ca3af;
      margin-top: 4px;
    }}
  </style>
  <script src="/static/js/helpers.js"></script>
</head>
<body>
  <div class="container">
    <p><a href="/">← Back to dashboard</a></p>
    <div class="card">
      <h1>Full Day Detail: {day}</h1>
      <div class="small-text">
        All probes logged for this day from connectivity.log. Use this page as
        a point-in-time snapshot to share with your provider.
      </div>
      <div class="toolbar">
        <button type="button" id="export-day">Export Day CSV</button>
      </div>
      <div class="table-wrapper">
        <table id="day-table">
          <thead>
            <tr>
              <th data-col="0">Timestamp</th>
              <th data-col="1">Target</th>
              <th data-col="2">Src IP</th>
              <th data-col="3">Public IP</th>
              <th data-col="4">Dst Host</th>
              <th data-col="5">Dst IP</th>
              <th data-col="6">Sent</th>
              <th data-col="7">Recv</th>
              <th data-col="8">Loss %</th>
              <th data-col="9">RTT Avg (ms)</th>
            </tr>
          </thead>
          <tbody>
            {rows_html}
          </tbody>
        </table>
      </div>
      <div class="small-text">
        Raw data source: {LOG_FILE}
      </div>
    </div>
  </div>

  <script>
    (function() {{
      const helpers = window.ConnectivityHelpers;
      const table = document.getElementById('day-table');
      const tbody = table.querySelector('tbody');
      const headerCells = table.querySelectorAll('thead th');
      const numericCols = new Set([6,7,8,9]);  // Sent, Recv, Loss, RTT
      const sortState = {{ index: 0, dir: 'asc' }};

      function getRowsArray() {{
        return Array.from(tbody.querySelectorAll('tr')).filter(tr => tr.querySelectorAll('td').length > 0);
      }}

      function sortTable(index) {{
        const rows = getRowsArray();
        const dir = (sortState.index === index && sortState.dir === 'asc') ? 'desc' : 'asc';
        sortState.index = index;
        sortState.dir = dir;

        rows.sort((a, b) => {{
          const aCells = a.querySelectorAll('td');
          const bCells = b.querySelectorAll('td');
          const A = aCells[index].textContent.trim();
          const B = bCells[index].textContent.trim();

          let cmp = 0;
          if (numericCols.has(index)) {{
            const na = Number(A);
            const nb = Number(B);
            if (!isNaN(na) && !isNaN(nb)) {{
              cmp = na - nb;
            }} else {{
              cmp = A.localeCompare(B);
            }}
          }} else {{
            cmp = A.localeCompare(B);
          }}

          return dir === 'asc' ? cmp : -cmp;
        }});

        // Re-attach in sorted order
        rows.forEach(r => tbody.appendChild(r));
      }}

      headerCells.forEach(th => {{
        th.addEventListener('click', () => {{
          const idx = Number(th.getAttribute('data-col'));
          sortTable(idx);
        }});
      }});

        function exportDayCsv() {{
          const headerRow = Array.from(headerCells).map(th => th.textContent.trim());
          const rows = getRowsArray().map(tr => (
            Array.from(tr.querySelectorAll('td')).map(td => td.textContent.trim())
          ));

          const csv = helpers.buildCsv(headerRow, rows);
          const blob = new Blob([csv], {{ type: 'text/csv' }});
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
        a.href = url;
        a.download = 'connectivity-{day}.csv';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }}

      document.getElementById('export-day').addEventListener('click', exportDayCsv);
    }})();
  </script>
</body>
</html>
"""

    def do_POST(self):
        if self.path == "/rebuild-summaries":
            reset_summary_cache()
            self._send_json({"ok": True, "message": "Summary cache cleared."})
            return
        if self.path != "/config":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}

        targets = (data.get("targets") or "").strip()
        interval = (data.get("interval_seconds") or "").strip()

        if interval and not interval.isdigit():
            interval = ""

        lines = []
        if targets:
            lines.append(f"TARGETS={targets}\n")
        if interval:
            lines.append(f"INTERVAL_SECONDS={interval}\n")

        try:
            if lines:
                with open(CONFIG_FILE, "w") as f:
                    f.writelines(lines)
            else:
                if os.path.exists(CONFIG_FILE):
                    os.remove(CONFIG_FILE)
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, status=500)
            return

        targets_display, interval_effective = read_config()
        self._send_json(
            {
                "ok": True,
                "targets_display": targets_display,
                "interval_seconds": interval_effective,
            }
        )


def main():
    mqtt_thread = None
    if build_settings_from_env is not None and MqttPublisher is not None:
        settings = build_settings_from_env()
        if settings.enabled:
            mqtt_thread = MqttPublisher(settings)
            mqtt_thread.start()
            print(
                "MQTT publishing enabled: sending measurements to"
                f" {settings.host}:{settings.port} on prefix {settings.topic_prefix}"
            )

    server_address = ("", WEB_PORT)
    httpd = HTTPServer(server_address, Handler)
    print(f"Starting webserver on port {WEB_PORT} ...")
    try:
        httpd.serve_forever()
    finally:
        if mqtt_thread:
            mqtt_thread.stop()


if __name__ == "__main__":
    main()

