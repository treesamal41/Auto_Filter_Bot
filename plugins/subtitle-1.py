"""
Subtitle downloader for Carla.
Adds a "Subtitles" button to IMDb search results.
Fetches .srt files from the OpenSubtitles API.

Setup:
  1. Get a FREE API key from https://www.opensubtitles.com/api
  2. Add it as env var: OPENSUBTITLES_API_KEY
"""

import io
import logging

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from info import OPENSUBTITLES_API_KEY
except ImportError:
    OPENSUBTITLES_API_KEY = ""

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

API_BASE = "https://api.opensubtitles.com/api/v1"
LANGS = [("English", "en"), ("Hindi", "hi"), ("Malayalam", "ml")]


def _headers():
    return {
        "Api-Key": OPENSUBTITLES_API_KEY,
        "Content-Type": "application/json",
        "User-Agent": "CarlaFilterBot/1.0",
    }


async def _search(session, imdb_id, lang):
    """Return list of subtitle entries for an IMDb id + language."""
    params = {"imdb_id": imdb_id, "languages": lang}
    try:
        async with session.get(
            f"{API_BASE}/subtitles", params=params, timeout=20
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
    except Exception as e:
        logger.exception(e)
        return []
    return data.get("data") or []


async def _download_link(session, file_id):
    """Exchange a file_id for a temporary download URL."""
    try:
        async with session.post(
            f"{API_BASE}/download", json={"file_id": file_id}, timeout=20
        ) as r:
            if r.status != 200:
                return None, None
            data = await r.json()
    except Exception as e:
        logger.exception(e)
        return None, None
    return data.get("link"), data.get("file_name")


@Client.on_callback_query(filters.regex(r"^subdl#"))
async def subtitle_langs(client, query):
    """Step 1 — language picker (sent as a new message, result buttons stay)."""
    try:
        _, imdb_id = query.data.split("#")
    except ValueError:
        return
    if not OPENSUBTITLES_API_KEY:
        return await query.answer(
            "Subtitle service not configured.", show_alert=True
        )
    await query.answer()
    buttons = [
        InlineKeyboardButton(name, callback_data=f"subget#{imdb_id}#{code}")
        for name, code in LANGS
    ]
    await query.message.reply_text(
        "📝 <b>Select subtitle language:</b>",
        reply_markup=InlineKeyboardMarkup([buttons]),
    )


@Client.on_callback_query(filters.regex(r"^subget#"))
async def subtitle_send(client, query):
    """Step 2 — fetch best subtitle and send the .srt file."""
    try:
        _, imdb_id, lang = query.data.split("#")
    except ValueError:
        return
    await query.answer("🔎 Searching subtitles...")
    try:
        await query.message.edit_text("🔎 <b>Searching subtitles...</b>")
    except Exception:
        pass

    async with aiohttp.ClientSession(headers=_headers()) as session:
        results = await _search(session, imdb_id, lang)
        if not results:
            return await query.message.edit_text(
                "😕 <b>No subtitles found for this language.</b>"
            )
        # pick the file with the most downloads
        files = results[0].get("attributes", {}).get("files", [])
        if not files:
            return await query.message.edit_text(
                "😕 <b>No subtitle files found.</b>"
            )
        best = max(files, key=lambda f: f.get("download_count", 0))
        link, fname = await _download_link(session, best["file_id"])
        if not link:
            return await query.message.edit_text(
                "⚠️ <b>Download failed (daily API limit may be over).</b>"
            )
        try:
            async with session.get(link, timeout=30) as r:
                content = await r.read()
        except Exception as e:
            logger.exception(e)
            return await query.message.edit_text(
                "⚠️ <b>Could not download the subtitle file.</b>"
            )

    bio = io.BytesIO(content)
    bio.name = fname or f"{imdb_id}_{lang}.srt"
    try:
        await client.send_document(
            chat_id=query.message.chat.id,
            document=bio,
            caption=f"📝 <b>Subtitles</b> ({dict(LANGS).get(lang, lang)})\n<code>{bio.name}</code>",
        )
        await query.message.delete()
    except Exception as e:
        logger.exception(e)
        await query.message.edit_text("⚠️ <b>Failed to send the file.</b>")
