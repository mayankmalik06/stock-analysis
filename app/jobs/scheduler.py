"""
app/jobs/scheduler.py

APScheduler-based morning workflow scheduler.

MILESTONE: 5 (Delivery and Scheduling)
STATUS: Stub — implementation coming in Milestone 5.

Planned morning schedule (IST):
  06:00 — Pull overnight announcements and RSS updates
  07:00 — Refresh exchange announcements and RSS
  08:00 — AI classification and event normalization
  08:45 — Build preliminary ranked list
  09:00 — Start polling pre-open data (every 2 minutes)
  09:14 — Freeze rankings and generate brief
  09:15 — Deliver final brief to Telegram
  09:20 — Optional second update with opening confirmation

Uses APScheduler with the Asia/Kolkata timezone.
"""

from apscheduler.schedulers.background import BackgroundScheduler


def create_scheduler() -> BackgroundScheduler:
    """
    Create and configure the morning workflow scheduler.
    Returns a configured (but not yet started) BackgroundScheduler.
    """
    raise NotImplementedError("Scheduler will be implemented in Milestone 5.")
