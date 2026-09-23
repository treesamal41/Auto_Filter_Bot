"""
Voice movie search for Carla.
User sends a voice message -> transcribed via Deepgram API ->
searched like a normal text query.
Needs DEEPGRAM_API_KEY env var on Heroku (free tier at deepgram.com).
"""

import logging
import os

import aiohttp
from pyrogram import Client, filters

try:
    from info import DEEPGRAM_API_KEY
except ImportError:
    DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "")

from plugins.pmfilter import auto_filter

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


async def transcribe_voice(file_path: str) -> str | None:
    """Transcribe ogg voice file with Deepgram API. Returns text or None."""
    if not DEEPGRAM_API_KEY:
        return None
    try:
        with open(file_path, "rb") as f:
            audio = f.read()
        headers = {
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "audio/ogg",
        }
        params = {
            "model": "nova-2",
            "smart_format": "true",
            "detect_language": "true",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepgram.com/v1/listen",
                data=audio, headers=headers, params=params, timeout=60,
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    try:
                        text = result["results"]["channels"][0][
                            "alternatives"][0]["transcript"]
                    except (KeyError, IndexError, TypeError):
                        text = ""
                    return text.strip() or None
                logger.error("Deepgram API status %s", resp.status)
    except Exception as e:
        logger.exception(e)
    return None


@Client.on_message(filters.voice & filters.incoming)
async def voice_search(client, message):
    if not DEEPGRAM_API_KEY:
        return await message.reply_text(
            "🎙️ Voice search setup cheythittilla. Admin DEEPGRAM_API_KEY set cheyyanam.")
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
