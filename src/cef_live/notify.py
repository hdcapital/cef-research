"""Alert channel adapter - email via Gmail (user's choice, brief §10.1).

Thin ``notify()`` interface so the channel stays swappable. Gmail is sent
over SMTP SSL with an App Password (never the account password). Required
env / repo secrets:

  GMAIL_ADDRESS       sending Gmail account
  GMAIL_APP_PASSWORD  16-char App Password (Google Account -> Security ->
                      2-Step Verification -> App passwords)
  ALERT_EMAIL_TO      recipient (may equal GMAIL_ADDRESS); comma-separated
                      for multiple recipients

Missing configuration is reported, never fatal: callers get False and the
message is echoed to the log so an unconfigured channel can't silently
swallow an alert.
"""

from __future__ import annotations

import mimetypes
import os
import smtplib
from email.message import EmailMessage
from email.mime.text import MIMEText
from pathlib import Path


def notify(subject: str, body: str, priority: str = "normal",
           attachments: list[str] | None = None,
           html: str | None = None) -> bool:
    """Send one alert, optionally with an HTML alternative and attachments.

    Returns True on delivery, False otherwise. A missing attachment is
    reported and skipped rather than silently dropping the whole message.
    `html`, when given, is sent as a multipart/alternative alongside the
    plain-text `body` - the text remains canonical, so the two must say
    the same thing.
    """
    addr = os.environ.get("GMAIL_ADDRESS", "")
    pw = os.environ.get("GMAIL_APP_PASSWORD", "")
    to = os.environ.get("ALERT_EMAIL_TO", "") or addr
    tag = {"critical": "[CEF-LIVE CRITICAL] ", "normal": "[CEF-LIVE] ",
           "heartbeat": "[CEF-LIVE pulse] "}.get(priority, "[CEF-LIVE] ")
    if not (addr and pw and to):
        print(f"notify (email unconfigured): {tag}{subject}\n{body[:500]}")
        return False
    if attachments or html:
        msg = EmailMessage()
        msg.set_content(body)
        if html:
            msg.add_alternative(html, subtype="html")
        # EmailMessage promotes itself to multipart/mixed when attachments
        # join an alternative part, so order (alternative first) matters
        for path in attachments or []:
            p = Path(path)
            if not p.exists():
                print(f"notify: attachment missing, skipped: {p}")
                continue
            ctype, _ = mimetypes.guess_type(p.name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(p.read_bytes(), maintype=maintype,
                               subtype=subtype, filename=p.name)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = tag + subject
    msg["From"] = addr
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as srv:
            srv.login(addr, pw)
            srv.sendmail(addr, [t.strip() for t in to.split(",")], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"notify FAILED ({exc}): {tag}{subject}")
        return False
