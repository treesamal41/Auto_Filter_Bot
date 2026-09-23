"""
Voice movie search for Carla.
User sends a voice message -> transcribed via Whisper API ->
searched like a normal text query.
Needs OPENAI_API_KEY env var on Heroku.
"""

import logging
import os

import aiohttp
from pyrogram import Client, filters

try:
    from info import OPENAI_API_KEY
except ImportError:
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

from plugins.pmfilter import auto_filter

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


async def transcribe_voice(file_path: str) -> str | None:
    """Transcribe ogg voice file with Whisper API. Returns text or None."""
    if not OPENAI_API_KEY:
        return None
    try:
        data = aiohttp.FormData()
        data.add_field("file", open(file_path, "rb"),
                       filename="voice.ogg", content_type="audio/ogg")
        data.add_field("model", "whisper-1")
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.openai.com/v1/audio/transcriptions",
                data=data, headers=headers, timeout=60,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    text = (result.get("text") or "").strip()
                    return text or None
                logger.error("Whisper API status %s", resp.status)
    except Exception as e:
        logger.exception(e)
    return None


@Client.on_message(filters.voice & filters.incoming)
async def voice_search(client, message):
    if not OPENAI_API_KEY:
        return await message.reply_text(
            "🎙️ Voice search setup cheythittilla. Admin OPENAI_API_KEY set cheyyanam.")
    status = await message.reply_text("🎙️ <b>Listening...</b>")
    path = None
    try:
        path = await message.download(file_name="/tmp/")
        text = await transcribe_voice(path)
        if not text:
            return await status.edit_text(
                "😕 Voice clear aayilla. Pinne try cheyyu.")
        await status.edit_text(f"🎙️ <b>Kettu:</b> <code>{text}</code>")
        # feed the transcribed text into normal search flow
        message.text = text
        await auto_filter(client, message)
        try:
            await status.delete()
        except Exception:
            pass
    except Exception as e:
        logger.exception(e)
        try:
            await status.edit_text("😕 Error. Pinne try cheyyu.")
        except Exception:
            pass
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
