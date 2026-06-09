from __future__ import annotations

import argparse
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
    rank_catalyst_rows,
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
        source_credibility_raw = form.get("source_credibility", "").strip()
        source_credibility = float(source_credibility_raw) if source_credibility_raw else None

        result = add_catalyst(
            settings=settings,
            symbol=form.get("symbol", ""),
            event_type=form.get("event_type", ""),
            event_date=form.get("event_date", ""),
            description=form.get("description", ""),
            source_url=form.get("source_url", ""),
            confidence_score=float(form.get("confidence_score", "")),
            source_credibility=source_credibility,
        )
        self._redirect(
            "/",
            notice=f"Added {result.symbol} catalyst with score {result.catalyst_score}.",
            notice_type="success",
        )

    def _handle_edit_catalyst(self, form: dict[str, str]) -> None:
        settings = get_settings()
        source_credibility_raw = form.get("source_credibility", "").strip()
        source_credibility = float(source_credibility_raw) if source_credibility_raw else None

        result = update_catalyst(
            settings=settings,
            catalyst_id=int(form.get("catalyst_id", "")),
            symbol=form.get("symbol", ""),
            event_type=form.get("event_type", ""),
            event_date=form.get("event_date", ""),
            description=form.get("description", ""),
            source_url=form.get("source_url", ""),
            confidence_score=float(form.get("confidence_score", "")),
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
    ranked_rows = rank_catalyst_rows(settings)
    coins = top_coin_rows(settings)
    notice = params.get("notice", [""])[0]
    notice_type = params.get("type", ["success"])[0]
    edit_catalyst = selected_catalyst_for_edit(settings, params)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Catalyst Research</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Local research dashboard</p>
        <h1>Crypto Catalyst Research</h1>
      </div>
      <div class="top-actions">
        <form method="post" action="/update-coins" class="inline-form">
          <input type="number" name="limit" min="1" max="500" value="{settings.cmc_limit}" aria-label="Coin limit">
          <button type="submit">Update Coins</button>
        </form>
        <a class="button secondary" href="/download">Download CSV</a>
      </div>
    </header>

    {render_notice(notice, notice_type)}

    <section class="metrics" aria-label="Research summary">
      <article>
        <span>Coins</span>
        <strong>{summary["coin_count"]}</strong>
      </article>
      <article>
        <span>Catalysts</span>
        <strong>{summary["catalyst_count"]}</strong>
      </article>
      <article>
        <span>Next {summary["window_days"]} Days</span>
        <strong>{summary["upcoming_count"]}</strong>
      </article>
    </section>

    <section class="layout">
      <div class="main-column">
        <section class="panel">
          <div class="section-heading">
            <div>
              <p class="eyebrow">Ranked output</p>
              <h2>Upcoming catalysts</h2>
            </div>
            <form method="post" action="/export">
              <input type="hidden" name="days" value="{settings.ranking_window_days}">
              <button type="submit" class="secondary">Export CSV</button>
            </form>
          </div>
          <p class="score-note">Scores rank catalyst relevance and timing. They are not buy/sell signals.</p>
          {render_ranked_table(ranked_rows)}
        </section>
      </div>

      <aside class="side-column">
        <section class="panel" id="catalyst-form">
          <p class="eyebrow">{"Edit catalyst" if edit_catalyst else "Manual entry"}</p>
          <h2>{"Edit catalyst" if edit_catalyst else "Add catalyst"}</h2>
          {render_edit_form(edit_catalyst) if edit_catalyst else render_add_form()}
        </section>

        <section class="panel">
          <p class="eyebrow">Universe preview</p>
          <h2>Top coins</h2>
          {render_coin_list(coins)}
        </section>
      </aside>
    </section>
  </main>
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
      <span>Confidence (0–100)</span>
      <input type="number" name="confidence_score" min="0" max="100" step="0.01" placeholder="80" required>
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
      <span>Confidence (0–100)</span>
      <input type="number" name="confidence_score" min="0" max="100" step="0.01" value="{attr(format_form_score(catalyst["confidence_score"]))}" required>
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
  <td><strong>{escape(str(row["symbol"]))}</strong><span>{escape(str(row["project_name"]))}</span></td>
  <td>{escape(humanize(str(row["event_type"])))}</td>
  <td>{escape(str(row["event_date"]))}<span>{row["days_until_event"]} days</span></td>
  <td>{escape(str(row["description"]))}<a href="{escape(str(row["source_url"]))}" target="_blank" rel="noreferrer">Source</a></td>
  <td>{format_unit_score_100(row["confidence_score"])}</td>
  <td><span class="score">{row["catalyst_score"]}</span></td>
  <td><a class="text-link" href="/?edit_id={row["catalyst_id"]}#catalyst-form">Edit</a></td>
</tr>"""
        for row in rows
    )

    return f"""<div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Asset</th>
        <th>Event</th>
        <th>Date</th>
        <th>Description</th>
        <th>Confidence (0–100)</th>
        <th>Score</th>
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
  <div>
    <strong>{escape(str(coin["symbol"]))}</strong>
    <span>{escape(str(coin["name"]))}</span>
  </div>
  <div class="coin-meta">
    <span>#{escape(str(coin["rank"] or "-"))}</span>
    <span>{escape(format_usd(coin["market_cap_usd"]))}</span>
  </div>
</li>"""
        for coin in coins
    )
    return f'<ul class="coin-list">{items}</ul>'


def humanize(value: str) -> str:
    return value.replace("_", " ").title()


def attr(value: object) -> str:
    return escape(str(value), quote=True)


def format_form_score(value: object) -> str:
    try:
        score = float(value) * 100
    except (TypeError, ValueError):
        return ""

    if score.is_integer():
        return str(int(score))
    return f"{score:.2f}".rstrip("0").rstrip(".")


def format_unit_score_100(value: object) -> str:
    try:
        return f"{float(value) * 100:.0f}"
    except (TypeError, ValueError):
        return "-"


def format_usd(value: object) -> str:
    if value is None:
        return "-"

    amount = float(value)
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:.2f}"


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
  --bg: #f7f8fb;
  --panel: #ffffff;
  --ink: #172033;
  --muted: #697386;
  --line: #dfe4ec;
  --accent: #0f766e;
  --accent-strong: #0b5f59;
  --accent-soft: #e7f5f3;
  --danger: #b42318;
  --danger-soft: #fff1f0;
  --shadow: 0 16px 36px rgba(20, 33, 61, 0.08);
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
}

.shell {
  width: min(1440px, calc(100% - 40px));
  margin: 0 auto;
  padding: 28px 0 40px;
}

.topbar,
.section-heading,
.layout,
.metrics,
.inline-form,
.form-grid {
  display: flex;
  gap: 16px;
}

.topbar {
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 22px;
}

.top-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
}

.eyebrow {
  margin: 0 0 4px;
  color: var(--accent);
  font-size: 0.76rem;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0;
}

h1,
h2 {
  margin: 0;
  letter-spacing: 0;
}

h1 {
  font-size: clamp(2rem, 4vw, 3.5rem);
  line-height: 1;
}

h2 {
  font-size: 1.2rem;
  line-height: 1.2;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-bottom: 18px;
}

.metrics article,
.panel {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}

.metrics article {
  padding: 18px;
}

.metrics span,
label span,
td span,
.coin-list span,
.empty-state span {
  color: var(--muted);
}

.metrics strong {
  display: block;
  margin-top: 4px;
  font-size: 2rem;
  line-height: 1;
}

.layout {
  align-items: flex-start;
}

.main-column {
  flex: 1 1 auto;
  min-width: 0;
}

.side-column {
  flex: 0 0 380px;
  display: grid;
  gap: 16px;
}

.panel {
  padding: 18px;
}

.section-heading {
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.score-note {
  margin: -4px 0 14px;
  color: var(--muted);
  font-size: 0.9rem;
}

button,
.button {
  min-height: 40px;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--accent);
  color: #fff;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 14px;
  font: inherit;
  font-weight: 800;
  text-decoration: none;
}

button:hover,
.button:hover {
  background: var(--accent-strong);
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

input,
select,
textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  font: inherit;
  padding: 10px 12px;
  outline: none;
}

textarea {
  resize: vertical;
}

input:focus,
select:focus,
textarea:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.inline-form input {
  width: 88px;
}

.stacked-form,
.coin-list {
  display: grid;
  gap: 12px;
  margin-top: 14px;
}

label {
  display: grid;
  gap: 6px;
  font-weight: 700;
}

label span {
  font-size: 0.82rem;
}

.form-grid {
  gap: 10px;
}

.form-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.notice {
  margin-bottom: 18px;
  border-radius: 8px;
  padding: 12px 14px;
  font-weight: 800;
}

.notice.success {
  background: var(--accent-soft);
  color: var(--accent-strong);
  border: 1px solid #bfe5df;
}

.notice.error {
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid #ffd0cc;
}

.table-wrap {
  overflow-x: auto;
  border: 1px solid var(--line);
  border-radius: 8px;
}

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 900px;
}

th,
td {
  border-bottom: 1px solid var(--line);
  padding: 13px 14px;
  text-align: left;
  vertical-align: top;
}

th {
  background: #f2f5f9;
  color: var(--muted);
  font-size: 0.76rem;
  text-transform: uppercase;
}

td {
  font-size: 0.94rem;
}

td span,
td a {
  display: block;
  margin-top: 4px;
}

td a {
  color: var(--accent);
  font-weight: 800;
  text-decoration: none;
}

.text-link {
  color: var(--accent);
  font-weight: 900;
  text-decoration: none;
}

.text-link:hover {
  text-decoration: underline;
}

tbody tr:last-child td {
  border-bottom: 0;
}

.score {
  display: inline-flex;
  min-width: 54px;
  min-height: 32px;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent-strong);
  font-weight: 900;
}

.coin-list {
  list-style: none;
  padding: 0;
}

.coin-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-bottom: 1px solid var(--line);
  padding-bottom: 12px;
}

.coin-list li:last-child {
  border-bottom: 0;
  padding-bottom: 0;
}

.coin-meta {
  text-align: right;
  flex: 0 0 auto;
}

.empty-state {
  display: grid;
  gap: 4px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 28px;
  background: #fbfcfe;
}

.empty-state.compact {
  margin-top: 14px;
  padding: 18px;
}

@media (max-width: 1040px) {
  .layout,
  .topbar {
    flex-direction: column;
  }

  .side-column {
    width: 100%;
    flex-basis: auto;
  }

  .top-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 720px) {
  .shell {
    width: min(100% - 24px, 1440px);
    padding-top: 18px;
  }

  .metrics,
  .form-grid {
    grid-template-columns: 1fr;
    display: grid;
  }

  .inline-form,
  .top-actions {
    width: 100%;
  }

  .inline-form input,
  .inline-form button,
  .top-actions .button {
    flex: 1 1 auto;
  }
}
"""


if __name__ == "__main__":
    main()
