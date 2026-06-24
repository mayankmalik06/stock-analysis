"""
app/delivery/telegram.py

Sends the morning brief to a Telegram chat using python-telegram-bot.

MILESTONE: 5 (Delivery and Scheduling)
STATUS: Stub — implementation coming in Milestone 5.

Planned behaviour:
- Read TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from settings.
- Split long briefs if needed (Telegram has a 4096-character limit).
- Send the Markdown-formatted brief.
- Log success or failure.
- Save brief to local file as backup if Telegram fails.
"""


async def send_telegram_brief(brief_markdown: str) -> bool:
    """
    Send the morning brief to Telegram.
    Returns True if sent successfully, False if failed.
    """
    raise NotImplementedError("Telegram delivery will be implemented in Milestone 5.")
