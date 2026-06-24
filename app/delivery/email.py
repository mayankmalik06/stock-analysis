"""
app/delivery/email.py

Email delivery as a backup channel for the morning brief.

MILESTONE: 5 (Delivery and Scheduling)
STATUS: Stub — implementation coming in Milestone 5.

Planned behaviour:
- Use SMTP or a transactional email service.
- Send the brief as a plain-text or HTML email.
- Use only when Telegram delivery fails or as a secondary channel.
"""


def send_email_brief(brief_markdown: str, recipient_email: str) -> bool:
    """
    Send the morning brief via email.
    Returns True if sent successfully, False if failed.
    """
    raise NotImplementedError("Email delivery will be implemented in Milestone 5.")
