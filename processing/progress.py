import asyncio
from core.config import PROGRESS_UPDATE_SECONDS

async def run_progress_updates(message, progress_data: dict, stop_event: asyncio.Event):
    from admin.panel import edit_message_smart
    last_text = ""
    while not stop_event.is_set():
        text = progress_data.get("text", "")
        if text and text != last_text:
            try:
                await edit_message_smart(message, text, reply_markup=None)
                last_text = text
            except Exception: pass
        await asyncio.sleep(PROGRESS_UPDATE_SECONDS)
