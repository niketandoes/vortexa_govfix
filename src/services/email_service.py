"""Email notification service for GovFix.

Provides:
- EmailProvider interface with LocalOutboxProvider and TestEmailProvider.
- Branded HTML and plain-text GovFix notification templates.
- Asynchronous post-commit dispatch with idempotency.
- Recording of send attempts in dedicated email_events table.
"""

from __future__ import annotations

import email.message
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.database import connect


OUTBOX_DIR = Path(__file__).resolve().parents[2] / ".outbox"


@dataclass
class EmailMessage:
    to: str
    subject: str
    html_body: str
    text_body: str
    template: str
    event_id: str | None = None
    metadata: dict[str, Any] | None = None


class EmailProvider(ABC):
    @abstractmethod
    def send(self, message: EmailMessage) -> bool:
        pass


class LocalOutboxProvider(EmailProvider):
    """Writes .eml and .html files to a gitignored outbox directory."""

    def __init__(self, outbox_dir: Path | None = None) -> None:
        self.outbox_dir = outbox_dir or OUTBOX_DIR
        self.outbox_dir.mkdir(parents=True, exist_ok=True)

    def send(self, message: EmailMessage) -> bool:
        try:
            self.outbox_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
            safe_to = message.to.replace("@", "_at_").replace(".", "_")
            event_tag = message.event_id or "event"
            base_filename = f"{timestamp}_{event_tag}_{safe_to}"

            # 1. Write HTML file for direct browser inspection
            html_path = self.outbox_dir / f"{base_filename}.html"
            html_content = (
                f"<!-- To: {message.to} | Subject: {message.subject} | Template: {message.template} -->\n"
                f"{message.html_body}"
            )
            html_path.write_text(html_content, encoding="utf-8")

            # 2. Write standard RFC 822 .eml file
            eml_path = self.outbox_dir / f"{base_filename}.eml"
            msg = email.message.EmailMessage()
            msg["Subject"] = message.subject
            msg["From"] = "GovFix Notifications <no-reply@govfix.local>"
            msg["To"] = message.to
            msg["Date"] = email.utils.formatdate(localtime=False)
            msg["X-GovFix-Template"] = message.template
            if message.event_id:
                msg["X-GovFix-Event-ID"] = message.event_id

            msg.set_content(message.text_body)
            msg.add_alternative(message.html_body, subtype="html")

            with open(eml_path, "wb") as f:
                f.write(msg.as_bytes())

            return True
        except Exception as e:
            print(f"[LocalOutboxProvider] Failed to write outbox message: {e}")
            return False


class TestEmailProvider(EmailProvider):
    """Provider for real test email products (SMTP / HTTP API).
    
    Falls back gracefully to LocalOutboxProvider if credentials are not configured.
    """

    def __init__(self) -> None:
        self.host = os.getenv("TEST_EMAIL_SMTP_HOST")
        self.port = int(os.getenv("TEST_EMAIL_SMTP_PORT", "587"))
        self.user = os.getenv("TEST_EMAIL_SMTP_USER")
        self.password = os.getenv("TEST_EMAIL_SMTP_PASS")
        self.from_addr = os.getenv("TEST_EMAIL_FROM", "no-reply@govfix.local")
        self.fallback = LocalOutboxProvider()

    def send(self, message: EmailMessage) -> bool:
        if not self.host or not self.user:
            # Fall back to local outbox
            return self.fallback.send(message)

        try:
            import smtplib
            msg = email.message.EmailMessage()
            msg["Subject"] = message.subject
            msg["From"] = self.from_addr
            msg["To"] = message.to
            msg.set_content(message.text_body)
            msg.add_alternative(message.html_body, subtype="html")

            with smtplib.SMTP(self.host, self.port) as server:
                server.starttls()
                server.login(self.user, self.password)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"[TestEmailProvider] SMTP send failed: {e}. Falling back to local outbox.")
            return self.fallback.send(message)


def get_email_provider() -> EmailProvider:
    provider_name = os.getenv("EMAIL_PROVIDER", "local_outbox").lower()
    if provider_name == "test_email":
        return TestEmailProvider()
    return LocalOutboxProvider()


# Branded Email Templates Builder
def _build_branded_html(title: str, preheader: str, body_content: str, cta_text: str | None = None, cta_url: str | None = None) -> str:
    cta_html = ""
    if cta_text and cta_url:
        cta_html = f"""
        <div style="margin: 28px 0; text-align: center;">
            <a href="{cta_url}" style="background-color: #2563eb; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block;">
                {cta_text}
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 24px;">
  <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background-color: #1e3a8a; padding: 20px 24px; border-bottom: 3px solid #3b82f6;">
      <h1 style="color: #ffffff; margin: 0; font-size: 20px; font-weight: 700; letter-spacing: -0.5px;">GovFix Municipal Grievance Redressal</h1>
      <p style="color: #bfdbfe; margin: 4px 0 0 0; font-size: 13px;">Official City Administration Notification System</p>
    </div>
    <div style="padding: 28px 24px; color: #1f2937; line-height: 1.6; font-size: 15px;">
      <h2 style="color: #111827; margin-top: 0; font-size: 18px; border-bottom: 1px solid #f3f4f6; padding-bottom: 12px;">{title}</h2>
      {body_content}
      {cta_html}
    </div>
    <div style="background-color: #f9fafb; padding: 16px 24px; border-top: 1px solid #e5e7eb; font-size: 12px; color: #6b7280; text-align: center;">
      <p style="margin: 0 0 4px 0;">This is an automated administrative notification. Please do not reply directly to this email.</p>
      <p style="margin: 0;">GovFix Municipal Platform &bull; Fictional Demonstration System &bull; 100% Isolated Data</p>
    </div>
  </div>
</body>
</html>"""


def render_template(template_name: str, context: dict[str, Any]) -> tuple[str, str, str]:
    """Render subject, html body, and text body for a given template."""
    name = context.get("name", "Citizen")
    req_id = context.get("request_id", "")
    title = context.get("title", "")
    location = context.get("location", "")
    category = context.get("category", "")
    deadline = context.get("deadline", "")
    reason = context.get("reason", "")
    link = context.get("link", "http://127.0.0.1:8000/citizen")

    if template_name == "account_verification":
        subj = "Verify your GovFix Citizen Account"
        html = _build_branded_html(
            title="Verify Your Email Address",
            preheader="Confirm your email to begin submitting grievances",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Thank you for creating an account on the GovFix Municipal Portal. To ensure authentic citizen communications and enable grievance filing, please verify your email address.</p>
            """,
            cta_text="Verify My Email",
            cta_url=link,
        )
        text = f"Dear {name},\n\nPlease verify your GovFix account by opening:\n{link}\n"

    elif template_name == "welcome":
        subj = "Welcome to GovFix Municipal Services"
        html = _build_branded_html(
            title="Welcome to GovFix",
            preheader="Your account is active",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your GovFix citizen account is verified and ready for use. You can now lodge municipal grievances, track live status updates, and review resolution progress.</p>
            """,
            cta_text="Visit Citizen Portal",
            cta_url=link,
        )
        text = f"Dear {name},\n\nWelcome to GovFix! Visit your dashboard at {link}.\n"

    elif template_name == "complaint_received":
        subj = f"Grievance Received: [{req_id}] {title}"
        html = _build_branded_html(
            title=f"Grievance Lodged: {req_id}",
            preheader="We have received your grievance",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your grievance has been successfully submitted to the Municipal Corporation.</p>
            <div style="background-color: #f8fafc; border-left: 4px solid #3b82f6; padding: 12px 16px; margin: 16px 0;">
                <p style="margin: 0 0 6px 0;"><strong>Case ID:</strong> {req_id}</p>
                <p style="margin: 0 0 6px 0;"><strong>Title:</strong> {title}</p>
                <p style="margin: 0 0 6px 0;"><strong>Location:</strong> {location}</p>
                <p style="margin: 0;"><strong>Expected Triage By:</strong> {deadline}</p>
            </div>
            <p>Our triage desk will review and assign your complaint to the relevant field team shortly.</p>
            """,
            cta_text="View Grievance Status",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour grievance [{req_id}] '{title}' at '{location}' has been received. Expected triage deadline: {deadline}.\nTrack at: {link}\n"

    elif template_name == "acknowledged_categorised":
        subj = f"Grievance Acknowledged & Categorised: [{req_id}]"
        html = _build_branded_html(
            title=f"Grievance Acknowledged: {req_id}",
            preheader="Your grievance has been acknowledged",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your grievance has been reviewed and acknowledged by the Municipal Triage Desk.</p>
            <p><strong>Category Confirmed:</strong> {category}</p>
            <p><strong>Statutory SLA Resolution Deadline:</strong> {deadline}</p>
            """,
            cta_text="Check Progress",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour complaint [{req_id}] was confirmed as '{category}'. Target deadline: {deadline}.\n"

    elif template_name == "assigned":
        team = context.get("team", "Municipal Field Operations Team")
        subj = f"Grievance Assigned to Field Team: [{req_id}]"
        html = _build_branded_html(
            title=f"Case Dispatched: {req_id}",
            preheader="Field team assigned",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your grievance has been assigned to the <strong>{team}</strong> for on-site inspection and remedial action.</p>
            <p>Work is scheduled to proceed in accordance with statutory municipal guidelines.</p>
            """,
            cta_text="Track Live Work",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour complaint [{req_id}] has been assigned to {team}.\n"

    elif template_name == "resolved":
        notes = context.get("notes", "Work completed according to municipal standards.")
        subj = f"Grievance Resolved: [{req_id}] - Please Review"
        html = _build_branded_html(
            title=f"Resolution Submitted: {req_id}",
            preheader="Action has been completed",
            body_content=f"""
            <p>Dear {name},</p>
            <p>The municipal field operations team has completed work on your grievance.</p>
            <div style="background-color: #f0fdf4; border-left: 4px solid #22c55e; padding: 12px 16px; margin: 16px 0;">
                <p style="margin: 0 0 6px 0; font-weight: 600; color: #166534;">Resolution Summary:</p>
                <p style="margin: 0; color: #15803d;">{notes}</p>
            </div>
            <p>Please review the completed work. If satisfied, confirm closure. If the issue persists, you may reopen the case within 7 days.</p>
            """,
            cta_text="Review & Confirm Closure",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour complaint [{req_id}] has been marked resolved. Summary: {notes}.\nConfirm or reopen within 7 days: {link}\n"

    elif template_name == "rejected":
        subj = f"Grievance Update: [{req_id}] Notice of Rejection"
        html = _build_branded_html(
            title=f"Grievance Notice: {req_id}",
            preheader="Case rejected with grounds",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your grievance has been reviewed and could not be accepted for the following reason:</p>
            <div style="background-color: #fef2f2; border-left: 4px solid #ef4444; padding: 12px 16px; margin: 16px 0;">
                <p style="margin: 0; color: #991b1b;"><strong>Reason:</strong> {reason}</p>
            </div>
            <p>If you believe this decision was made in error or contradicts municipal records, you have the right to submit a formal administrative appeal.</p>
            """,
            cta_text="Submit Administrative Appeal",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour complaint [{req_id}] was rejected. Reason: {reason}.\nYou may file an appeal at {link}.\n"

    elif template_name == "appeal_decision":
        note = context.get("appeal_approval_note", "Appeal approved upon administrative review.")
        subj = f"Appeal Decision: [{req_id}] Reinstated"
        html = _build_branded_html(
            title=f"Appeal Approved: {req_id}",
            preheader="Grievance reinstated for field action",
            body_content=f"""
            <p>Dear {name},</p>
            <p>Your appeal for grievance <strong>[{req_id}]</strong> has been reviewed by the Supervisory Authority and approved.</p>
            <p><strong>Supervisor Finding:</strong> {note}</p>
            <p>The grievance has been reinstated to active status and prioritized for field routing.</p>
            """,
            cta_text="View Reinstated Grievance",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYour appeal for [{req_id}] has been approved. Finding: {note}.\n"

    elif template_name == "new_assignment":
        subj = f"New Work Order Assigned: [{req_id}] {title}"
        html = _build_branded_html(
            title=f"Field Assignment: {req_id}",
            preheader="New work order assigned",
            body_content=f"""
            <p>Officer {name},</p>
            <p>A new grievance has been assigned to your operational queue.</p>
            <p><strong>Location:</strong> {location}</p>
            <p><strong>Category:</strong> {category}</p>
            <p><strong>SLA Resolution Deadline:</strong> {deadline}</p>
            """,
            cta_text="Open Field Workspace",
            cta_url="http://127.0.0.1:8000/staff/field",
        )
        text = f"Officer {name},\n\nNew assignment: [{req_id}] '{title}' at {location}. Deadline: {deadline}.\n"

    elif template_name == "reassignment":
        subj = f"Work Order Reassigned: [{req_id}] {title}"
        html = _build_branded_html(
            title=f"Reassigned Work Order: {req_id}",
            preheader="Work order reassigned by supervisor",
            body_content=f"""
            <p>Officer {name},</p>
            <p>Grievance <strong>[{req_id}]</strong> ({title}) has been reassigned to you for priority field resolution.</p>
            <p><strong>Location:</strong> {location}</p>
            <p><strong>Category:</strong> {category}</p>
            <p><strong>Statutory SLA Deadline:</strong> {deadline}</p>
            """,
            cta_text="Open Field Workspace",
            cta_url="http://127.0.0.1:8000/staff/field",
        )
        text = f"Officer {name},\n\nReassigned work order: [{req_id}] '{title}' at {location}. Deadline: {deadline}.\n"

    elif template_name == "needs_info":
        info_note = context.get("info_request_note", "Additional details required to process complaint.")
        subj = f"Action Required: More Information Needed on [{req_id}]"
        html = _build_branded_html(
            title=f"Information Requested: {req_id}",
            preheader="Field officer requested additional information",
            body_content=f"""
            <p>Dear {name},</p>
            <p>The municipal team reviewing your grievance <strong>[{req_id}]</strong> requires additional details:</p>
            <div style="background-color: #fefce8; border-left: 4px solid #eab308; padding: 12px 16px; margin: 16px 0;">
                <p style="margin: 0; color: #854d0e;"><strong>Note:</strong> {info_note}</p>
            </div>
            <p>Your SLA resolution timer is temporarily paused until details are provided.</p>
            """,
            cta_text="Provide Missing Details",
            cta_url=link,
        )
        text = f"Dear {name},\n\nAdditional information needed on [{req_id}]: {info_note}.\nSubmit details at: {link}\n"

    elif template_name == "escalation_level_1":
        subj = f"⚠️ SLA ALERT: Level 1 Escalation on [{req_id}]"
        html = _build_branded_html(
            title=f"Level 1 Escalation: {req_id}",
            preheader="100% SLA deadline exceeded",
            body_content=f"""
            <p>Supervisor {name},</p>
            <p>Grievance <strong>[{req_id}]</strong> ({title}) at <strong>{location}</strong> has exceeded 100% of its statutory SLA resolution window.</p>
            <p><strong>Category:</strong> {category}</p>
            <p><strong>Initial Deadline:</strong> {deadline}</p>
            <p>Please review workload allocation or initiate case reassignment.</p>
            """,
            cta_text="Open Supervisor Console",
            cta_url="http://127.0.0.1:8000/staff/supervisor",
        )
        text = f"Supervisor {name},\n\nSLA ALERT Level 1 on [{req_id}] '{title}'. Overdue deadline: {deadline}.\n"

    elif template_name == "escalation_level_2":
        subj = f"🚨 CRITICAL SLA BREACH: Level 2 Escalation on [{req_id}]"
        html = _build_branded_html(
            title=f"CRITICAL ESCALATION: {req_id}",
            preheader="150% SLA threshold breached",
            body_content=f"""
            <p>Supervisor {name},</p>
            <p style="color: #b91c1c; font-weight: 600;">URGENT EXECUTIVE ATTENTION REQUIRED</p>
            <p>Grievance <strong>[{req_id}]</strong> has breached 150% of the statutory SLA limit and is severely overdue.</p>
            <p>Immediate intervention and supervisor reassignment are mandated by municipal regulations.</p>
            """,
            cta_text="Open Supervisor Command Center",
            cta_url="http://127.0.0.1:8000/staff/supervisor",
        )
        text = f"Supervisor {name},\n\nCRITICAL SLA BREACH Level 2 on [{req_id}]. Immediate action required.\n"

    elif template_name == "account_invitation":
        role_name = context.get("role", "Staff Member")
        subj = "Invitation to GovFix Municipal Operations"
        html = _build_branded_html(
            title="Official Staff Invitation",
            preheader="Set up your municipal staff credentials",
            body_content=f"""
            <p>Dear {name},</p>
            <p>An administrative account has been provisioned for you with the role of <strong>{role_name}</strong>.</p>
            <p>Please click the button below to set your secure password and access your departmental console.</p>
            """,
            cta_text="Set Password & Activate Account",
            cta_url=link,
        )
        text = f"Dear {name},\n\nYou have been invited to GovFix as {role_name}. Set password at: {link}\n"

    elif template_name == "password_reset":
        subj = "Reset your GovFix Password"
        html = _build_branded_html(
            title="Password Reset Request",
            preheader="Reset your account password",
            body_content=f"""
            <p>Dear {name},</p>
            <p>We received a request to reset your password for your GovFix account. Click below to choose a new password.</p>
            """,
            cta_text="Reset Password",
            cta_url=link,
        )
        text = f"Dear {name},\n\nReset your password at: {link}\n"

    else:
        subj = f"GovFix Notification: [{req_id}]"
        html = _build_branded_html(
            title=f"Update for Grievance {req_id}",
            preheader="Grievance status changed",
            body_content=f"<p>Dear {name},</p><p>Your grievance [{req_id}] has a new update.</p>",
            cta_text="View Status",
            cta_url=link,
        )
        text = f"Dear {name},\n\nUpdate on [{req_id}]. View at: {link}\n"

    return subj, html, text


def dispatch_email_sync(
    recipient: str,
    template: str,
    context: dict[str, Any],
    event_id: str | None = None,
) -> bool:
    """Synchronously send an email through the active provider with idempotency and logging.
    
    Safe wrapper: exceptions never propagate to workflow transactions.
    """
    connection = connect()
    try:
        # Check idempotency: if already sent for this event_id, skip!
        if event_id:
            existing = connection.execute(
                "SELECT id, status FROM email_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing and existing["status"] == "sent":
                return True

        subject, html_body, text_body = render_template(template, context)
        message = EmailMessage(
            to=recipient,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            template=template,
            event_id=event_id,
            metadata=context,
        )

        provider = get_email_provider()
        success = False
        error_msg = None
        try:
            success = provider.send(message)
        except Exception as e:
            error_msg = str(e)
            success = False

        status_str = "sent" if success else "failed"
        now_str = datetime.now(UTC).isoformat()

        # Log send attempt to email_events table
        connection.execute(
            """
            INSERT INTO email_events (recipient, template, event_id, status, error, sent_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                status = excluded.status,
                error = excluded.error,
                sent_at = excluded.sent_at
            """,
            (
                recipient,
                template,
                event_id,
                status_str,
                error_msg,
                now_str,
                json.dumps(context),
            ),
        )
        connection.commit()
        return success
    except Exception as outer_e:
        print(f"[dispatch_email_sync] Unhandled error: {outer_e}")
        return False
    finally:
        connection.close()
