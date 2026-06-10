from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from src.config import get_settings
from src.models.catalyst import EventType
from src.services.catalyst_service import (
    add_catalyst,
    dashboard_summary,
    ensure_database,
    export_ranked_catalysts,
    get_catalyst_detail,
    market_overview,
    market_timeseries,
    price_history_status,
    rank_catalyst_rows,
    top_market_cap_snapshot_rows,
    top_coin_rows,
    update_catalyst,
    update_coin_universe,
)
from src.utils.logging import configure_logging


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "CryptoCatalystDashboard/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/":
            params = parse_qs(parsed.query)
            self._send_html(render_dashboard(params))
            return

        if parsed.path == "/static/styles.css":
            self._send_response(STYLES, content_type="text/css; charset=utf-8")
            return

        if parsed.path == "/download":
            self._send_download()
            return

        if parsed.path == "/health":
            self._send_response("ok\n", content_type="text/plain; charset=utf-8")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        try:
            form = self._read_form()
            if parsed.path == "/update-coins":
                self._handle_update_coins(form)
                return
            if parsed.path == "/add-catalyst":
                self._handle_add_catalyst(form)
                return
            if parsed.path == "/edit-catalyst":
                self._handle_edit_catalyst(form)
                return
            if parsed.path == "/export":
                self._handle_export(form)
                return
            if parsed.path == "/backfill-history":
                self._handle_backfill_history(form)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self._redirect("/", notice=str(exc), notice_type="error")

    def log_message(self, format: str, *args) -> None:
        return

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        parsed = parse_qs(raw_body, keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    def _handle_update_coins(self, form: dict[str, str]) -> None:
        settings = get_settings()
        limit = int(form.get("limit") or settings.cmc_limit)
        count = update_coin_universe(settings, limit=limit)
        self._redirect("/", notice=f"Updated {count} coins from CoinMarketCap.", notice_type="success")

    def _handle_add_catalyst(self, form: dict[str, str]) -> None:
        settings = get_settings()
        source_credibility = normalize_optional_score_input(form.get("source_credibility", ""))

        result = add_catalyst(
            settings=settings,
            symbol=form.get("symbol", ""),
            event_type=form.get("event_type", ""),
            event_date=form.get("event_date", ""),
            description=form.get("description", ""),
            source_url=form.get("source_url", ""),
            confidence_score=normalize_score_input(form.get("confidence_score", "")),
            source_credibility=source_credibility,
        )
        self._redirect(
            "/",
            notice=f"Added {result.symbol} catalyst with score {result.catalyst_score}.",
            notice_type="success",
        )

    def _handle_edit_catalyst(self, form: dict[str, str]) -> None:
        settings = get_settings()
        source_credibility = normalize_optional_score_input(form.get("source_credibility", ""))

        result = update_catalyst(
            settings=settings,
            catalyst_id=int(form.get("catalyst_id", "")),
            symbol=form.get("symbol", ""),
            event_type=form.get("event_type", ""),
            event_date=form.get("event_date", ""),
            description=form.get("description", ""),
            source_url=form.get("source_url", ""),
            confidence_score=normalize_score_input(form.get("confidence_score", "")),
            source_credibility=source_credibility,
        )
        self._redirect(
            "/",
            notice=f"Updated {result.symbol} catalyst with score {result.catalyst_score}.",
            notice_type="success",
        )

    def _handle_export(self, form: dict[str, str]) -> None:
        settings = get_settings()
        days = int(form.get("days") or settings.ranking_window_days)
        output_path = export_ranked_catalysts(settings, days=days)
        self._redirect("/", notice=f"Exported ranked CSV to {output_path}.", notice_type="success")

    def _handle_backfill_history(self, form: dict[str, str]) -> None:
        mode = form.get("mode", "refresh")
        script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_price_history.py"
        args = [sys.executable, str(script)]
        if mode == "refresh":
            args.append("--refresh")
        subprocess.Popen(args, start_new_session=True)
        label = "3-day refresh" if mode == "refresh" else "full backfill"
        self._redirect("/", notice=f"Price history {label} started in background.", notice_type="success")

    def _send_download(self) -> None:
        settings = get_settings()
        output_path = export_ranked_catalysts(settings)
        payload = output_path.read_bytes()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv")
        self.send_header("Content-Disposition", 'attachment; filename="ranked_catalysts.csv"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, path: str, notice: str, notice_type: str) -> None:
        query = urlencode({"notice": notice, "type": notice_type})
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", f"{path}?{query}")
        self.end_headers()

    def _send_html(self, html: str) -> None:
        self._send_response(html, content_type="text/html; charset=utf-8")

    def _send_response(self, body: str, content_type: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def render_dashboard(params: dict[str, list[str]]) -> str:
    settings = get_settings()
    ensure_database(settings)

    summary = dashboard_summary(settings)
    overview = market_overview(settings)
    market_cap_rows = top_market_cap_snapshot_rows(settings)
    market_series = market_timeseries(settings)
    ranked_rows = rank_catalyst_rows(settings)
    coins = top_coin_rows(settings)
    cg_status = price_history_status(settings)
    notice = params.get("notice", [""])[0]
    notice_type = params.get("type", ["success"])[0]
    edit_catalyst = selected_catalyst_for_edit(settings, params)

    top_score = "&mdash;"
    if ranked_rows:
        try:
            top_score = format_number(ranked_rows[0]["adjusted_score"])
        except (KeyError, TypeError):
            pass

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Research</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar" aria-label="Primary navigation">
      <div class="brand">
        <span class="brand-icon">CR</span>
        <div class="brand-text">
          <strong>Crypto Research</strong>
          <small>Catalyst Intelligence</small>
        </div>
      </div>
      <div class="nav-scroll">
        <p class="nav-section-label">MENU</p>
        <nav class="nav-list">
          <a class="active" href="/"><span class="nav-icon">&#9646;</span>Dashboard</a>
          <a href="#ranked-catalysts"><span class="nav-icon">&#9889;</span>Catalysts</a>
          <a href="#market-overview"><span class="nav-icon">&#9650;</span>Market</a>
          <a href="#catalyst-form"><span class="nav-icon">&#43;</span>Add Catalyst</a>
        </nav>
        <p class="nav-section-label">DATA</p>
        <nav class="nav-list">
          <a href="#coingecko-panel"><span class="nav-icon">&#9698;</span>Price History</a>
        </nav>
        <p class="nav-section-label">GENERAL</p>
        <nav class="nav-list">
          <a href="/download"><span class="nav-icon">&#8595;</span>Download CSV</a>
        </nav>
      </div>
      <div class="sidebar-foot">
        <span class="sidebar-foot-badge">Research Mode</span>
        <p>No live trading enabled</p>
      </div>
    </aside>

    <main class="shell">
      <header class="topbar">
        <div class="topbar-title">
          <p class="eyebrow">Welcome back, Austin</p>
          <h1>Crypto Research</h1>
        </div>
        <div class="top-actions">
          <form method="post" action="/update-coins" class="inline-form">
            <input type="number" name="limit" min="1" max="500" value="{settings.cmc_limit}" aria-label="Coin limit">
            <button type="submit">+ Update Coins</button>
          </form>
          <a class="button secondary" href="/download">&#8595; Download CSV</a>
        </div>
      </header>

      {render_notice(notice, notice_type)}

      <div class="stats-row">
        <article class="stat-card stat-green">
          <div class="stat-card-header">
            <span class="stat-card-label">Total Coins</span>
            <div class="stat-card-icon icon-green">&#9670;</div>
          </div>
          <strong class="stat-card-value">{summary["coin_count"]}</strong>
          <div class="stat-card-trend">Tracked universe</div>
        </article>
        <article class="stat-card stat-blue">
          <div class="stat-card-header">
            <span class="stat-card-label">Catalysts</span>
            <div class="stat-card-icon icon-blue">&#9889;</div>
          </div>
          <strong class="stat-card-value">{summary["catalyst_count"]}</strong>
          <div class="stat-card-trend">Total logged</div>
        </article>
        <article class="stat-card stat-amber">
          <div class="stat-card-header">
            <span class="stat-card-label">Upcoming Events</span>
            <div class="stat-card-icon icon-amber">&#9201;</div>
          </div>
          <strong class="stat-card-value">{summary["upcoming_count"]}</strong>
          <div class="stat-card-trend">Next {summary["window_days"]} days</div>
        </article>
        <article class="stat-card stat-purple">
          <div class="stat-card-header">
            <span class="stat-card-label">Top Score</span>
            <div class="stat-card-icon icon-purple">&#9733;</div>
          </div>
          <strong class="stat-card-value">{top_score}</strong>
          <div class="stat-card-trend">Best opportunity score</div>
        </article>
      </div>

      <section class="panel market-overview" id="market-overview" aria-label="Market overview">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Live data</p>
            <h2>Market Overview</h2>
          </div>
        </div>
        {render_market_overview(overview)}
        {render_market_graphs(market_series)}
        {render_market_cap_chart(market_cap_rows)}
      </section>

      <section class="main-content-grid">
        <div class="main-column">
          <section class="panel" id="ranked-catalysts">
            <div class="section-heading">
              <div>
                <p class="eyebrow">Ranked output</p>
                <h2>Upcoming Catalysts</h2>
              </div>
              <form method="post" action="/export">
                <input type="hidden" name="days" value="{settings.ranking_window_days}">
                <button type="submit" class="secondary">Export CSV</button>
              </form>
            </div>
            <p class="score-note">Scores rank catalyst relevance and timing &mdash; not buy/sell signals.</p>
            {render_ranked_table(ranked_rows)}
          </section>
        </div>

        <aside class="side-column">
          {render_edit_panel(edit_catalyst) if edit_catalyst else ""}

          <section class="panel" id="catalyst-form">
            <p class="eyebrow">Manual entry</p>
            <h2>Add Catalyst</h2>
            {render_add_form()}
          </section>

          {render_coingecko_panel(cg_status)}

          <section class="panel">
            <div class="section-heading">
              <div>
                <p class="eyebrow">Universe preview</p>
                <h2>Top Coins</h2>
              </div>
            </div>
            {render_coin_list(coins)}
          </section>
        </aside>
      </section>
    </main>
  </div>
</body>
</html>"""


def render_notice(notice: str, notice_type: str) -> str:
    if not notice:
        return ""

    css_class = "notice error" if notice_type == "error" else "notice success"
    return f'<div class="{css_class}" role="status">{escape(notice)}</div>'


def selected_catalyst_for_edit(settings, params: dict[str, list[str]]) -> dict[str, object] | None:
    edit_id = params.get("edit_id", [""])[0]
    if not edit_id:
        return None

    try:
        return get_catalyst_detail(settings, int(edit_id))
    except ValueError:
        return None


def render_market_overview(overview: dict[str, object]) -> str:
    return f"""<div class="overview-grid">
  <article>
    <span>Total market cap</span>
    <strong>{format_usd_or_na(overview["total_tracked_market_cap"])}</strong>
  </article>
  <article>
    <span>24h volume</span>
    <strong>{format_usd_or_na(overview["total_tracked_volume_24h"])}</strong>
  </article>
  <article>
    <span>Tracked coins</span>
    <strong>{overview["tracked_coin_count"]}</strong>
  </article>
  <article>
    <span>Last snapshot</span>
    <strong>{format_timestamp(overview["latest_snapshot_timestamp"])}</strong>
  </article>
  <article>
    <span>BTC 7d return</span>
    <strong>{format_pct(overview["btc_return_7d_pct"])}</strong>
  </article>
  <article>
    <span>ETH 7d return</span>
    <strong>{format_pct(overview["eth_return_7d_pct"])}</strong>
  </article>
</div>"""


def render_market_graphs(series: list[dict[str, object]]) -> str:
    return f"""<div class="graph-grid">
  <article class="graph-card">
    <div class="graph-card-header">
      <span class="graph-card-label">Market cap trend</span>
      <span class="graph-card-delta">{format_series_delta(series, "total_market_cap")}</span>
    </div>
    {render_line_chart(series, "total_market_cap")}
  </article>
  <article class="graph-card">
    <div class="graph-card-header">
      <span class="graph-card-label">24h volume trend</span>
      <span class="graph-card-delta">{format_series_delta(series, "total_volume_24h")}</span>
    </div>
    {render_line_chart(series, "total_volume_24h")}
  </article>
</div>"""


def render_line_chart(series: list[dict[str, object]], key: str) -> str:
    values = chart_values(series, key)
    if len(values) < 2:
        return """<div class="chart-empty">No data yet</div>"""

    width = 520
    height = 130
    padding = 10
    min_value = min(values)
    max_value = max(values)
    value_range = max(max_value - min_value, 1)
    step = (width - padding * 2) / max(len(values) - 1, 1)

    points = []
    for index, value in enumerate(values):
        x = padding + index * step
        y = height - padding - ((value - min_value) / value_range) * (height - padding * 2)
        points.append(f"{x:.2f},{y:.2f}")

    polyline = " ".join(points)
    area_points = f"{padding},{height - padding} {polyline} {width - padding},{height - padding}"
    return f"""<svg class="line-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{key.replace("_", " ")} chart">
  <defs>
    <linearGradient id="grad-{key}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#16a34a" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#16a34a" stop-opacity="0.01"/>
    </linearGradient>
  </defs>
  <polygon points="{area_points}" fill="url(#grad-{key})"></polygon>
  <polyline points="{polyline}"></polyline>
</svg>"""


def format_series_delta(series: list[dict[str, object]], key: str) -> str:
    values = chart_values(series, key)
    if len(values) < 2 or values[0] <= 0:
        return "N/A"

    delta = ((values[-1] / values[0]) - 1) * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def chart_values(series: list[dict[str, object]], key: str = "value") -> list[float]:
    values = []
    for point in series:
        if point.get(key) is None:
            continue
        try:
            values.append(float(point[key] or 0))
        except (TypeError, ValueError):
            continue
    return values


def render_market_cap_chart(rows: list[dict[str, object]]) -> str:
    if not rows:
        return """<div class="empty-state compact">
  <strong>No market snapshots yet</strong>
  <span>Run Update Coins to store the first market snapshot.</span>
</div>"""

    max_market_cap = max(float(row["market_cap_usd"] or 0) for row in rows) or 1
    bars = "\n".join(
        f"""<li>
  <span class="bar-label">{escape(str(row["symbol"]))}</span>
  <div class="bar-track"><span style="width: {bar_width(row["market_cap_usd"], max_market_cap)}%"></span></div>
  <span class="bar-value">{format_usd_or_na(row["market_cap_usd"])}</span>
</li>"""
        for row in rows
    )
    return f"""<div class="market-chart">
  <p class="chart-heading">Top 10 by tracked market cap</p>
  <ul>{bars}</ul>
</div>"""


def render_edit_panel(catalyst: dict[str, object]) -> str:
    return f"""<section class="panel" id="edit-catalyst-form">
  <p class="eyebrow">Edit catalyst</p>
  <h2>Edit Catalyst</h2>
  {render_edit_form(catalyst)}
</section>"""


def render_add_form() -> str:
    default_date = (date.today() + timedelta(days=14)).isoformat()

    return f"""<form class="stacked-form" method="post" action="/add-catalyst">
  <label>
    <span>Symbol</span>
    <input name="symbol" placeholder="ETH" required>
  </label>
  <label>
    <span>Event type</span>
    <select name="event_type" required>
      {render_event_options("mainnet_upgrade")}
    </select>
  </label>
  <label>
    <span>Event date</span>
    <input type="date" name="event_date" value="{default_date}" required>
  </label>
  <label>
    <span>Description</span>
    <textarea name="description" rows="4" placeholder="Mainnet upgrade target date" required></textarea>
  </label>
  <label>
    <span>Source URL</span>
    <input type="url" name="source_url" placeholder="https://project.org/news" required>
  </label>
  <div class="form-grid">
    <label>
      <span>Confidence score (0&ndash;100)</span>
      <input type="number" name="confidence_score" min="0" max="100" step="0.01" placeholder="80" required>
      <small>Stored internally as 0.0&ndash;1.0.</small>
    </label>
    <label>
      <span>Source override</span>
      <input type="number" name="source_credibility" min="0" max="100" step="0.01" placeholder="Optional">
    </label>
  </div>
  <button type="submit" class="wide">Add Catalyst</button>
</form>"""


def render_edit_form(catalyst: dict[str, object]) -> str:
    return f"""<form class="stacked-form" method="post" action="/edit-catalyst">
  <input type="hidden" name="catalyst_id" value="{attr(catalyst["id"])}">
  <label>
    <span>Symbol</span>
    <input name="symbol" value="{attr(catalyst["symbol"])}" required>
  </label>
  <label>
    <span>Event type</span>
    <select name="event_type" required>
      {render_event_options(str(catalyst["event_type"]))}
    </select>
  </label>
  <label>
    <span>Event date</span>
    <input type="date" name="event_date" value="{attr(catalyst["event_date"])}" required>
  </label>
  <label>
    <span>Description</span>
    <textarea name="description" rows="4" placeholder="Mainnet upgrade target date" required>{escape(str(catalyst["description"]))}</textarea>
  </label>
  <label>
    <span>Source URL</span>
    <input type="url" name="source_url" value="{attr(catalyst["source_url"])}" required>
  </label>
  <div class="form-grid">
    <label>
      <span>Confidence score (0&ndash;100)</span>
      <input type="number" name="confidence_score" min="0" max="100" step="0.01" value="{attr(format_form_score(catalyst["confidence_score"]))}" required>
      <small>Stored internally as 0.0&ndash;1.0.</small>
    </label>
    <label>
      <span>Source override</span>
      <input type="number" name="source_credibility" min="0" max="100" step="0.01" value="{attr(format_form_score(catalyst["source_credibility"]))}">
    </label>
  </div>
  <div class="form-actions">
    <button type="submit">Save Changes</button>
    <a class="button secondary" href="/">Cancel</a>
  </div>
</form>"""


def render_event_options(selected: str) -> str:
    return "\n".join(
        f'<option value="{event.value}"{option_selected(event.value, selected)}>{humanize(event.value)}</option>'
        for event in EventType
    )


def option_selected(value: str, selected: str) -> str:
    return " selected" if value == selected else ""


def render_ranked_table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return """<div class="empty-state">
  <strong>No upcoming catalysts yet</strong>
  <span>Add a catalyst or adjust your source data, then export when ready.</span>
</div>"""

    body = "\n".join(
        f"""<tr>
  <td class="td-asset"><strong>{escape(str(row["symbol"]))}</strong><span>{escape(str(row["project_name"]))}</span></td>
  <td class="td-catalyst"><span class="td-desc" title="{attr(row["description"])}">{escape(str(row["description"]))}</span><a href="{escape(str(row["source_url"]))}" target="_blank" rel="noreferrer">&#8599;&nbsp;Source</a></td>
  <td class="td-date"><strong>{escape(str(row["event_date"]))}</strong><span>{row["days_until_event"]}d away</span></td>
  <td><span class="score neutral">{format_number(row["catalyst_score"])}</span></td>
  <td>{render_pct_colored(row["return_7d_pct"])}</td>
  <td>{render_pct_colored(row["return_14d_pct"])}</td>
  <td>{render_pct_colored(row["return_30d_pct"])}</td>
  <td>{render_pct_colored(row["volume_change_pct"])}</td>
  <td>{render_pct_colored(row["btc_relative_return_pct"])}</td>
  <td>{render_pct_colored(row["eth_relative_return_pct"])}</td>
  <td><span class="quant-cell">{format_pct(row.get("realized_vol_30d"))}</span></td>
  <td>{render_pct_colored(row.get("ma_20d_distance_pct"))}</td>
  <td><span class="quant-cell">{format_number(row.get("btc_correlation_30d"))}</span></td>
  <td><span class="score penalty">{format_number(row["priced_in_penalty"])}</span></td>
  <td><span class="score">{format_number(row["adjusted_score"])}</span></td>
  <td>{render_status_badge(row["adjusted_score"], row["priced_in_penalty"])}</td>
  <td><a class="button table-action secondary" href="/?edit_id={row["catalyst_id"]}#edit-catalyst-form">Edit</a></td>
</tr>"""
        for row in rows
    )

    return f"""<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Asset</th>
        <th>Catalyst</th>
        <th>Date</th>
        <th>Cat. Score</th>
        <th>7D Return</th>
        <th>14D Return</th>
        <th>30D Return</th>
        <th>Vol Change</th>
        <th>vs BTC</th>
        <th>vs ETH</th>
        <th>Vol 30d</th>
        <th>MA Dist</th>
        <th>BTC Corr</th>
        <th>Priced-In</th>
        <th>Adj. Score</th>
        <th>Status</th>
        <th>Action</th>
      </tr>
    </thead>
    <tbody>
      {body}
    </tbody>
  </table>
</div>"""


def render_coin_list(coins: list[dict[str, object]]) -> str:
    if not coins:
        return """<div class="empty-state compact">
  <strong>No coins loaded</strong>
  <span>Use Update Coins after adding your CMC key.</span>
</div>"""

    items = "\n".join(
        f"""<li>
  <span class="coin-list-rank">{escape(str(coin["rank"] or "—"))}</span>
  <div class="coin-list-info">
    <strong>{escape(str(coin["symbol"]))}</strong>
    <span>{escape(str(coin["name"]))}</span>
  </div>
  <span class="coin-list-cap">{escape(format_usd(coin["market_cap_usd"]))}</span>
</li>"""
        for coin in coins
    )
    return f'<ul class="coin-list">{items}</ul>'


def render_coingecko_panel(status: dict[str, object]) -> str:
    api_key_configured = bool(status["api_key_configured"])
    coins_with_history = int(status["coins_with_history"])
    last_backfilled = status["last_backfilled"]
    total_rows = int(status["total_rows"])

    key_badge_class = "cg-key-ok" if api_key_configured else "cg-key-missing"
    key_label = "API key set" if api_key_configured else "No API key"
    key_hint = "" if api_key_configured else '<p class="cg-hint">Add <code>COINGECKO_API_KEY=your_key</code> to <code>.env</code> to unlock higher rate limits.</p>'

    last_date = str(last_backfilled) if last_backfilled else "Never"

    return f"""<section class="panel" id="coingecko-panel">
  <div class="section-heading">
    <div>
      <p class="eyebrow">Market data</p>
      <h2>Price History</h2>
    </div>
    <span class="cg-key-badge {key_badge_class}">{key_label}</span>
  </div>
  {key_hint}
  <div class="cg-stats">
    <div class="cg-stat">
      <span>Coins with history</span>
      <strong>{coins_with_history}</strong>
    </div>
    <div class="cg-stat">
      <span>Total daily rows</span>
      <strong>{format_number(total_rows)}</strong>
    </div>
    <div class="cg-stat cg-stat-wide">
      <span>Last backfilled</span>
      <strong>{last_date}</strong>
    </div>
  </div>
  <div class="cg-actions">
    <form method="post" action="/backfill-history" class="inline-form">
      <input type="hidden" name="mode" value="refresh">
      <button type="submit">&#8635; Refresh (3d)</button>
    </form>
    <form method="post" action="/backfill-history" class="inline-form">
      <input type="hidden" name="mode" value="full">
      <button type="submit" class="secondary">Full Backfill</button>
    </form>
  </div>
</section>"""


def render_pct_colored(value: object) -> str:
    if value is None:
        return '<span class="pct-neutral">N/A</span>'
    try:
        v = float(value)
        css = "pct-positive" if v > 0 else "pct-negative" if v < 0 else "pct-neutral"
        sign = "+" if v > 0 else ""
        return f'<span class="{css}">{sign}{v:.1f}%</span>'
    except (TypeError, ValueError):
        return '<span class="pct-neutral">N/A</span>'


def render_status_badge(adjusted_score: object, priced_in_penalty: object) -> str:
    label, css_class = compute_status(adjusted_score, priced_in_penalty)
    return f'<span class="status-badge {css_class}">{label}</span>'


def compute_status(adjusted_score: object, priced_in_penalty: object) -> tuple[str, str]:
    try:
        score = float(adjusted_score or 0)
        penalty = float(priced_in_penalty or 0)
    except (TypeError, ValueError):
        return ("Skip", "status-skip")
    if penalty >= 35:
        return ("Crowded", "status-crowded")
    if score >= 65:
        return ("Watchlist", "status-watchlist")
    if score >= 40:
        return ("Research", "status-research")
    if score >= 20:
        return ("Monitor", "status-monitor")
    return ("Skip", "status-skip")


def humanize(value: str) -> str:
    return value.replace("_", " ").title()


def attr(value: object) -> str:
    return escape(str(value), quote=True)


def normalize_score_input(raw_value: str) -> float:
    return clamp_score_input(raw_value) / 100


def normalize_optional_score_input(raw_value: str) -> float | None:
    if raw_value.strip() == "":
        return None
    return normalize_score_input(raw_value)


def clamp_score_input(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 0.0

    return max(0.0, min(100.0, value))


def format_form_score(value: object) -> str:
    try:
        score = float(value) * 100
    except (TypeError, ValueError):
        return ""

    if score.is_integer():
        return str(int(score))
    return f"{score:.2f}".rstrip("0").rstrip(".")


def format_number(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_pct(value: object) -> str:
    if value is None:
        return "N/A"

    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def format_usd(value: object) -> str:
    if value is None:
        return "-"

    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if amount >= 1_000_000_000_000:
        return f"${amount / 1_000_000_000_000:.1f}T"
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:.2f}"


def format_usd_or_na(value: object) -> str:
    if value is None:
        return "N/A"
    return format_usd(value)


def format_timestamp(value: object) -> str:
    if not value:
        return "N/A"
    return str(value).replace("T", " ")[:19]


def bar_width(value: object, max_value: float) -> float:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0
    return round(max(3.0, min(100.0, (amount / max_value) * 100)), 2)


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    configure_logging()
    ensure_database(get_settings())
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    print(f"Dashboard running at http://{host}:{port}")
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local crypto catalyst dashboard")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    run_server(host=args.host, port=args.port)


STYLES = """
:root {
  color-scheme: light;
  --bg: #f1f5f1;
  --panel: #ffffff;
  --ink: #0d1b0f;
  --muted: #6b7280;
  --line: #e4e8e4;
  --accent: #16a34a;
  --accent-strong: #15803d;
  --accent-soft: #f0fdf4;
  --accent-dim: #dcfce7;
  --warning: #d97706;
  --warning-soft: #fffbeb;
  --danger: #dc2626;
  --danger-soft: #fef2f2;
  --sidebar: #052e16;
  --sidebar-active: rgba(34, 197, 94, 0.14);
  --sidebar-muted: #4ade80;
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.07), 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow: 0 4px 12px rgba(0, 0, 0, 0.06), 0 2px 4px rgba(0, 0, 0, 0.04);
  --shadow-lg: 0 10px 28px rgba(0, 0, 0, 0.09), 0 4px 10px rgba(0, 0, 0, 0.05);
  --radius: 12px;
  --radius-sm: 8px;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-width: 320px;
  background: var(--bg);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}

/* ─── APP SHELL ─────────────────────────────────── */

.app-shell {
  display: grid;
  grid-template-columns: 242px minmax(0, 1fr);
  min-height: 100vh;
}

/* ─── SIDEBAR ────────────────────────────────────── */

.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--sidebar);
  color: #f0fdf4;
  overflow: hidden;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 22px 18px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.brand-icon {
  display: inline-flex;
  width: 36px;
  height: 36px;
  align-items: center;
  justify-content: center;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-weight: 900;
  font-size: 0.8rem;
  flex-shrink: 0;
}

.brand-text strong {
  display: block;
  font-size: 0.9rem;
  font-weight: 700;
  color: #f0fdf4;
  line-height: 1.2;
}

.brand-text small {
  display: block;
  font-size: 0.7rem;
  color: rgba(74, 222, 128, 0.65);
  margin-top: 2px;
}

.nav-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 10px 10px 16px;
}

.nav-section-label {
  margin: 18px 8px 6px;
  color: rgba(255, 255, 255, 0.28);
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.nav-section-label:first-child {
  margin-top: 6px;
}

.nav-list {
  display: grid;
  gap: 2px;
}

.nav-list a {
  display: flex;
  align-items: center;
  gap: 9px;
  border-radius: var(--radius-sm);
  color: rgba(240, 253, 244, 0.6);
  font-weight: 600;
  font-size: 0.845rem;
  padding: 8px 12px;
  text-decoration: none;
  transition: background 0.14s, color 0.14s;
}

.nav-icon {
  font-size: 0.9rem;
  width: 16px;
  text-align: center;
  flex-shrink: 0;
  opacity: 0.65;
  transition: opacity 0.14s;
}

.nav-list a:hover {
  background: rgba(255, 255, 255, 0.07);
  color: #f0fdf4;
}

.nav-list a:hover .nav-icon {
  opacity: 1;
}

.nav-list a.active {
  background: var(--sidebar-active);
  color: #86efac;
  font-weight: 700;
}

.nav-list a.active .nav-icon {
  opacity: 1;
}

.sidebar-foot {
  padding: 14px 18px 20px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  flex-shrink: 0;
}

.sidebar-foot-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: rgba(34, 197, 94, 0.12);
  border: 1px solid rgba(34, 197, 94, 0.22);
  border-radius: 20px;
  padding: 4px 10px;
  font-size: 0.7rem;
  font-weight: 700;
  color: #86efac;
}

.sidebar-foot-badge::before {
  content: "●";
  font-size: 0.45rem;
  color: #4ade80;
}

.sidebar-foot p {
  margin: 8px 0 0;
  font-size: 0.72rem;
  color: rgba(240, 253, 244, 0.35);
}

/* ─── MAIN SHELL ─────────────────────────────────── */

.shell {
  width: min(100% - 40px, 1580px);
  margin: 0 auto;
  padding: 28px 0 52px;
}

/* ─── TOPBAR ─────────────────────────────────────── */

.topbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}

.topbar-title .eyebrow {
  margin: 0 0 4px;
}

.topbar-title h1 {
  margin: 0;
  font-size: clamp(1.6rem, 2.6vw, 2.3rem);
  font-weight: 800;
  letter-spacing: -0.025em;
  line-height: 1.1;
}

.top-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
}

.inline-form {
  display: flex;
  gap: 8px;
  align-items: center;
}

.inline-form input {
  width: 72px;
}

/* ─── TYPOGRAPHY ─────────────────────────────────── */

.eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  font-size: 0.7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.07em;
}

h1, h2 {
  margin: 0;
  letter-spacing: -0.01em;
}

h2 {
  font-size: 1.05rem;
  font-weight: 700;
  line-height: 1.25;
}

/* ─── STAT CARDS ─────────────────────────────────── */

.stats-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}

.stat-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 18px 20px 16px;
  position: relative;
  overflow: hidden;
  transition: box-shadow 0.15s, transform 0.15s;
}

.stat-card:hover {
  box-shadow: var(--shadow-lg);
  transform: translateY(-1px);
}

.stat-card::after {
  content: "";
  position: absolute;
  bottom: 0;
  left: 20px;
  right: 20px;
  height: 3px;
  border-radius: 999px 999px 0 0;
}

.stat-green::after { background: #16a34a; }
.stat-blue::after  { background: #3b82f6; }
.stat-amber::after { background: #f59e0b; }
.stat-purple::after { background: #8b5cf6; }

.stat-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.stat-card-label {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--muted);
}

.stat-card-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.9rem;
  flex-shrink: 0;
}

.icon-green  { background: #dcfce7; color: #15803d; }
.icon-blue   { background: #dbeafe; color: #1d4ed8; }
.icon-amber  { background: #fef3c7; color: #b45309; }
.icon-purple { background: #ede9fe; color: #6d28d9; }

.stat-card-value {
  display: block;
  font-size: 2.1rem;
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.03em;
  color: var(--ink);
  margin-bottom: 8px;
}

.stat-card-trend {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--muted);
}

/* ─── PANELS ─────────────────────────────────────── */

.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 20px;
}

.market-overview {
  margin-bottom: 20px;
}

.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.score-note {
  margin: -8px 0 16px;
  padding: 9px 14px;
  background: #f6f9f6;
  border-left: 3px solid var(--line);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  color: var(--muted);
  font-size: 0.82rem;
}

/* ─── MARKET OVERVIEW GRID ───────────────────────── */

.overview-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 10px;
}

.overview-grid article {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 14px;
  background: #fafbfa;
  transition: border-color 0.14s;
}

.overview-grid article:hover {
  border-color: rgba(22, 163, 74, 0.4);
}

.overview-grid span {
  display: block;
  font-size: 0.74rem;
  color: var(--muted);
  font-weight: 600;
  margin-bottom: 5px;
}

.overview-grid strong {
  display: block;
  font-size: 1rem;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
}

/* ─── CHART CARDS ────────────────────────────────── */

.graph-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.graph-card {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  background: #fafbfa;
  padding: 16px;
}

.graph-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.graph-card-label {
  font-size: 0.78rem;
  color: var(--muted);
  font-weight: 600;
}

.graph-card-delta {
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--accent-strong);
  background: var(--accent-dim);
  padding: 3px 9px;
  border-radius: 20px;
}

.line-chart {
  display: block;
  width: 100%;
  height: 120px;
}

.line-chart polyline {
  fill: none;
  stroke: var(--accent);
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2.5;
}

.chart-empty {
  display: grid;
  min-height: 120px;
  place-items: center;
  border: 1px dashed var(--line);
  border-radius: var(--radius-sm);
  color: var(--muted);
  font-size: 0.82rem;
}

/* ─── MARKET CAP CHART ───────────────────────────── */

.market-chart {
  margin-top: 18px;
  border-top: 1px solid var(--line);
  padding-top: 16px;
}

.chart-heading {
  font-size: 0.72rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0 0 12px;
}

.market-chart ul {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.market-chart li {
  display: grid;
  grid-template-columns: 52px minmax(120px, 1fr) 84px;
  align-items: center;
  gap: 10px;
}

.bar-label {
  font-weight: 700;
  font-size: 0.8rem;
}

.bar-track {
  height: 7px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--accent-dim);
}

.bar-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
}

.bar-value {
  text-align: right;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--muted);
}

/* ─── LAYOUT ─────────────────────────────────────── */

.main-content-grid {
  display: flex;
  align-items: flex-start;
  gap: 18px;
}

.main-column {
  flex: 1 1 auto;
  min-width: 0;
}

.side-column {
  flex: 0 0 368px;
  display: grid;
  gap: 16px;
}

/* ─── BUTTONS ────────────────────────────────────── */

button,
.button {
  min-height: 38px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 16px;
  font: inherit;
  font-size: 0.845rem;
  font-weight: 700;
  text-decoration: none;
  transition: background 0.14s, box-shadow 0.14s;
  white-space: nowrap;
}

button:hover,
.button:hover {
  background: var(--accent-strong);
  box-shadow: 0 2px 10px rgba(22, 163, 74, 0.28);
}

button.secondary,
.button.secondary {
  background: #fff;
  color: var(--accent);
}

button.secondary:hover,
.button.secondary:hover {
  background: var(--accent-soft);
}

button.wide {
  width: 100%;
}

/* ─── TABLE ──────────────────────────────────────── */

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 1380px;
}

th,
td {
  border-bottom: 1px solid var(--line);
  padding: 11px 13px;
  text-align: left;
  vertical-align: middle;
}

th {
  background: #f4f7f4;
  color: var(--muted);
  font-size: 0.69rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  white-space: nowrap;
}

td {
  font-size: 0.875rem;
  background: var(--panel);
}

tbody tr {
  transition: background 0.1s;
}

tbody tr:hover td {
  background: #f7faf7;
}

tbody tr:last-child td {
  border-bottom: 0;
}

.td-asset strong {
  display: block;
  font-weight: 700;
  font-size: 0.875rem;
  color: var(--ink);
}

.td-asset span {
  display: block;
  font-size: 0.75rem;
  color: var(--muted);
  margin-top: 2px;
}

.td-catalyst .td-desc {
  display: block;
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.82rem;
  color: var(--ink);
}

.td-catalyst a {
  display: inline-block;
  margin-top: 4px;
  font-size: 0.72rem;
  color: var(--accent);
  font-weight: 700;
  text-decoration: none;
}

.td-catalyst a:hover {
  text-decoration: underline;
}

.td-date strong {
  display: block;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--ink);
}

.td-date span {
  display: block;
  font-size: 0.72rem;
  color: var(--muted);
  margin-top: 2px;
}

/* ─── COLORED PERCENTAGES ────────────────────────── */

.pct-positive {
  color: #16a34a;
  font-weight: 700;
}

.pct-negative {
  color: #dc2626;
  font-weight: 700;
}

.pct-neutral {
  color: var(--muted);
}

/* ─── QUANT CELLS ────────────────────────────────── */

.quant-cell {
  font-size: 0.78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}

/* ─── SCORE BADGES ───────────────────────────────── */

.score {
  display: inline-flex;
  min-width: 44px;
  min-height: 26px;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: var(--accent-dim);
  color: var(--accent-strong);
  font-weight: 800;
  font-size: 0.78rem;
}

.score.neutral {
  background: #f0f4f0;
  color: var(--muted);
}

.score.penalty {
  background: #fee2e2;
  color: #dc2626;
}

/* ─── STATUS BADGES ──────────────────────────────── */

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 0.7rem;
  font-weight: 700;
  white-space: nowrap;
}

.status-badge::before {
  content: "●";
  font-size: 0.42rem;
}

.status-watchlist {
  background: #dcfce7;
  color: #15803d;
}

.status-research {
  background: #fef3c7;
  color: #92400e;
}

.status-crowded {
  background: #fee2e2;
  color: #b91c1c;
}

.status-monitor {
  background: #dbeafe;
  color: #1e40af;
}

.status-skip {
  background: #f3f4f6;
  color: #6b7280;
}

/* ─── TABLE ACTION ───────────────────────────────── */

.table-action {
  min-height: 28px;
  padding: 0 10px;
  font-size: 0.75rem;
}

/* ─── FORMS ──────────────────────────────────────── */

.stacked-form {
  display: grid;
  gap: 14px;
  margin-top: 16px;
}

label {
  display: grid;
  gap: 5px;
  font-weight: 600;
  font-size: 0.875rem;
}

label span {
  font-size: 0.76rem;
  color: var(--muted);
  font-weight: 600;
}

label small {
  font-size: 0.71rem;
  color: var(--muted);
  font-weight: 500;
  line-height: 1.4;
}

input,
select,
textarea {
  width: 100%;
  border: 1.5px solid var(--line);
  border-radius: var(--radius-sm);
  background: #fff;
  color: var(--ink);
  font: inherit;
  font-size: 0.875rem;
  padding: 9px 12px;
  outline: none;
  transition: border-color 0.14s, box-shadow 0.14s;
}

textarea {
  resize: vertical;
}

input:focus,
select:focus,
textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(22, 163, 74, 0.1);
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.form-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

/* ─── NOTICE ─────────────────────────────────────── */

.notice {
  margin-bottom: 20px;
  border-radius: var(--radius-sm);
  padding: 11px 14px;
  font-weight: 700;
  font-size: 0.845rem;
  display: flex;
  align-items: center;
  gap: 10px;
}

.notice.success {
  background: var(--accent-soft);
  color: var(--accent-strong);
  border: 1px solid #bbf7d0;
}

.notice.error {
  background: #fef2f2;
  color: #b91c1c;
  border: 1px solid #fecaca;
}

/* ─── COIN LIST ──────────────────────────────────── */

.coin-list {
  list-style: none;
  padding: 0;
  margin: 8px 0 0;
  display: grid;
  gap: 2px;
}

.coin-list li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  transition: background 0.1s;
}

.coin-list li:hover {
  background: var(--accent-soft);
}

.coin-list-rank {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #f0f4f0;
  color: var(--muted);
  font-size: 0.68rem;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.coin-list-info {
  flex: 1;
  min-width: 0;
}

.coin-list-info strong {
  display: block;
  font-size: 0.84rem;
  font-weight: 700;
  color: var(--ink);
}

.coin-list-info span {
  display: block;
  font-size: 0.73rem;
  color: var(--muted);
  margin-top: 1px;
}

.coin-list-cap {
  text-align: right;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--ink);
  flex-shrink: 0;
}

/* ─── EMPTY STATES ───────────────────────────────── */

.empty-state {
  display: grid;
  gap: 6px;
  text-align: center;
  padding: 32px 20px;
  border: 1px dashed var(--line);
  border-radius: var(--radius-sm);
  background: #fafbfa;
}

.empty-state strong {
  font-size: 0.9rem;
  color: var(--ink);
}

.empty-state span {
  font-size: 0.8rem;
  color: var(--muted);
}

.empty-state.compact {
  padding: 18px 16px;
  margin-top: 10px;
}

/* ─── COINGECKO PANEL ────────────────────────────── */

.cg-key-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 0.7rem;
  font-weight: 700;
  white-space: nowrap;
  flex-shrink: 0;
}

.cg-key-badge::before {
  content: "●";
  font-size: 0.42rem;
}

.cg-key-ok {
  background: #dcfce7;
  color: #15803d;
}

.cg-key-missing {
  background: #fef3c7;
  color: #92400e;
}

.cg-hint {
  margin: -4px 0 12px;
  font-size: 0.77rem;
  color: var(--muted);
  line-height: 1.5;
}

.cg-hint code {
  background: #f0f4f0;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.75rem;
  color: var(--ink);
  font-family: "SF Mono", "Fira Code", ui-monospace, monospace;
}

.cg-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 14px;
}

.cg-stat {
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  background: #fafbfa;
}

.cg-stat-wide {
  grid-column: 1 / -1;
}

.cg-stat span {
  display: block;
  font-size: 0.72rem;
  color: var(--muted);
  font-weight: 600;
  margin-bottom: 3px;
}

.cg-stat strong {
  display: block;
  font-size: 1rem;
  font-weight: 700;
  color: var(--ink);
  letter-spacing: -0.01em;
}

.cg-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.cg-actions .inline-form {
  flex: 1;
  min-width: 0;
}

.cg-actions button {
  width: 100%;
}

/* ─── RESPONSIVE ─────────────────────────────────── */

@media (max-width: 1080px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .sidebar {
    position: static;
    height: auto;
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    padding: 12px 16px;
    gap: 16px;
  }

  .nav-scroll {
    padding: 0;
    flex: 1;
  }

  .nav-list {
    grid-template-columns: repeat(4, auto);
    gap: 4px;
  }

  .nav-section-label {
    display: none;
  }

  .sidebar-foot {
    display: none;
  }

  .stats-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .overview-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .main-content-grid {
    flex-direction: column;
  }

  .side-column {
    width: 100%;
    flex-basis: auto;
  }

  .topbar {
    flex-direction: column;
  }

  .top-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 720px) {
  .shell {
    width: min(100% - 24px, 1580px);
    padding-top: 20px;
  }

  .stats-row,
  .overview-grid,
  .graph-grid,
  .form-grid {
    grid-template-columns: 1fr;
  }
}
"""


if __name__ == "__main__":
    main()
