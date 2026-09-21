from __future__ import annotations

import html
import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _safe(value: object) -> str:
    return html.escape(str(value), quote=True)


def _time_label(value: object) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return "—"
    return parsed.astimezone(timezone.utc).strftime("%b %-d, %Y · %H:%M UTC")


def _reminder_label(key: object) -> str:
    value = str(key or "")
    labels = {
        "cleaner-fourteen-days-before-checkin:": "14-day reminder",
        "cleaner-five-days-before-checkin:": "5-day reminder",
        "cleaner-last-minute-booking:": "Last-minute reminder",
        "cleaner-day-before:": "Day-before reminder",
    }
    return next(
        (label for prefix, label in labels.items() if value.startswith(prefix)),
        "Reminder",
    )


def load_snapshot(client: Any, property_id: str) -> dict[str, Any]:
    from .adapters.firestore import _namespace

    prefix = f"host_ops_{_namespace(property_id)}"
    runs = [
        item.to_dict()
        for item in client.collection(f"{prefix}_run_history").stream()
    ]
    runs.sort(key=lambda item: str(item.get("completed_at", "")), reverse=True)

    messages = [
        item.to_dict() for item in client.collection(f"{prefix}_sms_outbox").stream()
    ]
    messages.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    today = date.today()
    cutoff = today + timedelta(days=60)
    cleaning_dates: set[date] = set()
    for item in client.collection(f"{prefix}_actions").stream():
        action = item.to_dict()
        if action.get("type") != "cleaner_sms":
            continue
        raw_date = action.get("payload", {}).get("check_out")
        if not raw_date:
            continue
        try:
            cleaning_date = datetime.fromisoformat(str(raw_date)).date()
        except ValueError:
            continue
        if today <= cleaning_date <= cutoff:
            cleaning_dates.add(cleaning_date)

    return {
        "runs": runs[:20],
        "messages": messages[:30],
        "message_counts": Counter(
            str(item.get("delivery_status", "unknown")) for item in messages
        ),
        "cleaning_dates": sorted(cleaning_dates),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def render_dashboard(snapshot: dict[str, Any]) -> str:
    runs = snapshot.get("runs", [])
    messages = snapshot.get("messages", [])
    counts = snapshot.get("message_counts", {})
    cleaning_dates = snapshot.get("cleaning_dates", [])
    latest = runs[0] if runs else {}
    latest_status = str(latest.get("status", "No history yet"))
    healthy = latest_status == "succeeded"

    cleaning_html = "".join(
        f"<li><span>{_safe(value.strftime('%a'))}</span> "
        f"{_safe(value.strftime('%b %-d, %Y'))}</li>"
        for value in cleaning_dates
    ) or "<li class='muted'>No confirmed cleanings in the next 60 days.</li>"

    run_rows = "".join(
        "<tr>"
        f"<td>{_safe(_time_label(run.get('completed_at')))}</td>"
        f"<td><span class='pill good'>{_safe(run.get('status', 'unknown'))}</span></td>"
        f"<td>{_safe(run.get('stays_polled', 0))}</td>"
        f"<td>{_safe(run.get('reminders_queued', 0))}</td>"
        f"<td>{_safe(run.get('messages_submitted', 0))}</td>"
        "</tr>"
        for run in runs
    ) or "<tr><td colspan='5' class='muted'>History will appear after the next scheduled poll.</td></tr>"

    message_rows = "".join(
        "<tr>"
        f"<td>{_safe(_time_label(message.get('created_at')))}</td>"
        f"<td>{_safe(_reminder_label(message.get('idempotency_key')))}</td>"
        f"<td><span class='pill'>{_safe(message.get('delivery_status', 'unknown'))}</span></td>"
        "</tr>"
        for message in messages
    ) or "<tr><td colspan='3' class='muted'>No SMS records yet.</td></tr>"

    status_class = "good" if healthy else "warn"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Host Ops status</title>
<style>
:root{{--ink:#17211b;--muted:#68736c;--paper:#f3f5f1;--card:#fff;--green:#166534;--line:#dde3dc;--amber:#92400e}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 system-ui,-apple-system,sans-serif}}
main{{max-width:900px;margin:auto;padding:22px 14px 60px}} h1{{font-size:28px;margin:0}} h2{{font-size:17px;margin:0 0 12px}}
.top{{display:flex;justify-content:space-between;gap:12px;align-items:end;margin-bottom:18px}} .muted{{color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}} .card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 2px #00000008}}
.metric strong{{display:block;font-size:25px}} .pill{{display:inline-block;border-radius:999px;background:#eef2ef;padding:3px 8px;font-size:12px}} .pill.good,.good{{color:var(--green)}} .warn{{color:var(--amber)}}
ul{{list-style:none;padding:0;margin:0;columns:2}} li{{padding:6px 0}} li span{{display:inline-block;width:36px;color:var(--muted)}}
.table-wrap{{overflow:auto}} table{{width:100%;border-collapse:collapse;white-space:nowrap}} th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--line)}} th{{font-size:12px;color:var(--muted)}}
@media(max-width:600px){{.grid{{grid-template-columns:1fr}} .top{{display:block}} ul{{columns:1}} h1{{font-size:24px}}}}
</style></head><body><main>
<div class="top"><div><h1>Host Ops</h1><div class="muted">Cleaner reminder status</div></div><div class="muted">Updated {_safe(_time_label(snapshot.get('generated_at')))}</div></div>
<section class="grid">
<div class="card metric"><span class="muted">Latest poll</span><strong class="{status_class}">{_safe(latest_status.title())}</strong><small>{_safe(_time_label(latest.get('completed_at')))}</small></div>
<div class="card metric"><span class="muted">Upcoming cleanings</span><strong>{len(cleaning_dates)}</strong><small>Next 60 days</small></div>
<div class="card metric"><span class="muted">SMS submitted</span><strong>{_safe(counts.get('submitted', 0))}</strong><small>{_safe(counts.get('not_sent', 0))} pending · {_safe(counts.get('sending', 0))} sending</small></div>
</section>
<section class="card"><h2>Confirmed cleaning dates</h2><ul>{cleaning_html}</ul></section>
<section class="card"><h2>Poll history</h2><div class="table-wrap"><table><thead><tr><th>Completed</th><th>Status</th><th>Stays</th><th>Queued</th><th>Sent</th></tr></thead><tbody>{run_rows}</tbody></table></div></section>
<section class="card"><h2>Message records</h2><div class="muted">Names, phone numbers, and message bodies are intentionally hidden.</div><div class="table-wrap"><table><thead><tr><th>Created</th><th>Type</th><th>Status</th></tr></thead><tbody>{message_rows}</tbody></table></div></section>
</main></body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    client: Any = None
    property_id = "default-property"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._respond(200, "text/plain; charset=utf-8", b"ok\n")
            return
        if self.path not in {"/", "/index.html"}:
            self._respond(404, "text/plain; charset=utf-8", b"not found\n")
            return
        body = render_dashboard(load_snapshot(self.client, self.property_id)).encode()
        self._respond(200, "text/html; charset=utf-8", body)

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


def main() -> None:
    from google.cloud import firestore

    DashboardHandler.client = firestore.Client()
    DashboardHandler.property_id = os.environ.get(
        "HOST_OPS_PROPERTY_ID", "default-property"
    )
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8080"))), DashboardHandler
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
