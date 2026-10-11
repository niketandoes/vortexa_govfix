"""Developer routes for inspecting local outbox emails without third-party services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from src.database import connect
from src.services.email_service import OUTBOX_DIR


router = APIRouter(tags=["Developer"])


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@router.get("/api/v1/dev/outbox")
def list_outbox_emails(connection=Depends(get_db)) -> dict[str, Any]:
    """Return all recorded email send attempts from email_events."""
    rows = connection.execute(
        """
        SELECT id, recipient, template, event_id, status, error, sent_at, metadata_json
        FROM email_events
        ORDER BY id DESC
        """
    ).fetchall()

    records = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata_json"])
        except Exception:
            pass
        records.append(
            {
                "id": r["id"],
                "recipient": r["recipient"],
                "template": r["template"],
                "event_id": r["event_id"],
                "status": r["status"],
                "error": r["error"],
                "sent_at": r["sent_at"],
                "metadata": meta,
            }
        )
    return {"success": True, "count": len(records), "data": records}


@router.get("/dev/outbox", response_class=HTMLResponse)
def view_outbox_ui(connection=Depends(get_db)) -> HTMLResponse:
    """Render a developer UI inspecting all emails in the local outbox."""
    rows = connection.execute(
        """
        SELECT id, recipient, template, event_id, status, error, sent_at, metadata_json
        FROM email_events
        ORDER BY id DESC
        """
    ).fetchall()

    rows_html = ""
    for r in rows:
        badge_color = "#16a34a" if r["status"] == "sent" else "#dc2626"
        meta = {}
        try:
            meta = json.loads(r["metadata_json"])
        except Exception:
            pass

        rows_html += f"""
        <tr style="border-bottom: 1px solid #e5e7eb;">
            <td style="padding: 12px; font-family: monospace;">{r["id"]}</td>
            <td style="padding: 12px;"><strong>{r["recipient"]}</strong></td>
            <td style="padding: 12px;"><span style="background: #e0f2fe; color: #0369a1; padding: 3px 8px; border-radius: 4px; font-size: 12px;">{r["template"]}</span></td>
            <td style="padding: 12px; font-family: monospace; font-size: 13px;">{r["event_id"] or '-'}</td>
            <td style="padding: 12px;"><span style="color: {badge_color}; font-weight: 600;">{r["status"].upper()}</span></td>
            <td style="padding: 12px; font-size: 13px; color: #6b7280;">{r["sent_at"]}</td>
        </tr>
        """

    # Check files on disk in .outbox
    outbox_files = []
    if OUTBOX_DIR.exists():
        outbox_files = [f.name for f in OUTBOX_DIR.glob("*.html")]

    files_html = "".join([f"<li><code>{f}</code></li>" for f in outbox_files[:20]])

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>GovFix Dev Outbox</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; margin: 0; padding: 32px; color: #1e293b; }}
        .container {{ max-width: 1000px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 24px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        h1 {{ margin-top: 0; font-size: 22px; color: #0f172a; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; text-align: left; }}
        th {{ background: #f1f5f9; padding: 12px; font-weight: 600; border-bottom: 2px solid #cbd5e1; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #e2e8f0; padding-bottom: 16px; }}
        .tag {{ background: #fef3c7; color: #92400e; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>📬 GovFix Local Email Outbox</h1>
                <p style="margin: 4px 0 0 0; color: #64748b; font-size: 14px;">Captures outgoing system emails locally without third-party credentials.</p>
            </div>
            <span class="tag">DEV ONLY</span>
        </div>

        <h3 style="margin-top: 24px;">Dispatched Events ({len(rows)})</h3>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Recipient</th>
                    <th>Template</th>
                    <th>Event ID</th>
                    <th>Status</th>
                    <th>Sent At</th>
                </tr>
            </thead>
            <tbody>
                {rows_html if rows_html else '<tr><td colspan="6" style="padding: 24px; text-align: center; color: #94a3b8;">No emails dispatched yet.</td></tr>'}
            </tbody>
        </table>

        <h3 style="margin-top: 32px;">Files in <code>.outbox/</code> ({len(outbox_files)})</h3>
        <ul style="font-size: 13px; color: #475569;">
            {files_html if files_html else '<li>No HTML/EML files written yet.</li>'}
        </ul>
    </div>
</body>
</html>"""
    return HTMLResponse(content=html)
