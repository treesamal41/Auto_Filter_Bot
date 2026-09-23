"""
Subtitle downloader for Carla.
Adds a "Subtitles" button to IMDb search results.
Fetches .srt files from the OpenSubtitles API,
and Malayalam subs from Team GOAT (malayalamsubtitles.in).

Setup:
  1. Get a FREE API key from https://www.opensubtitles.com/api
  2. Add it as env var: OPENSUBTITLES_API_KEY
"""

import io
import logging
import re
import time

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

try:
    from info import OPENSUBTITLES_API_KEY
except ImportError:
    from os import environ
    OPENSUBTITLES_API_KEY = environ.get('OPENSUBTITLES_API_KEY', '')

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


# --- Team GOAT (malayalamsubtitles.in) — keyless Malayalam source ---
GOAT_API = "https://wp.malayalamsubtitles.in/wp-json/wp/v2"
_goat_cache = {"at": 0.0, "items": []}
GOAT_CACHE_TTL = 6 * 3600  # refresh catalog every 6 hours


async def _goat_catalog(session):
    """Fetch (and cache) the full Team GOAT release catalog."""
    now = time.time()
    if _goat_cache["items"] and now - _goat_cache["at"] < GOAT_CACHE_TTL:
        return _goat_cache["items"]
    items = []
    try:
        for page in range(1, 8):
            async with session.get(
                f"{GOAT_API}/release",
                params={
                    "per_page": 100,
                    "page": page,
                    "_fields": "id,slug,title,acf.imdb,acf.subtitle,acf.language",
                },
                timeout=20,
            ) as r:
                if r.status != 200:
                    break
                data = await r.json()
                if not data:
                    break
                items.extend(data)
    except Exception as e:
        logger.exception(e)
        return _goat_cache["items"]
    if items:
        _goat_cache["at"] = now
        _goat_cache["items"] = items
    return items


async def _fetch_goat(session, imdb_id):
    """Return (file bytes, filename) from Team GOAT, or (None, None)."""
    items = await _goat_catalog(session)
    matches = []
    for it in items:
        acf = it.get("acf") or {}
        if acf.get("imdb") == imdb_id and acf.get("subtitle"):
            matches.append(it)
    if not matches:
        return None, None
    # prefer Malayalam-language entries
    matches.sort(
        key=lambda it: 0
        if "മലയാളം" in str((it.get("acf") or {}).get("language", ""))
        else 1
    )
    best = matches[0]
    media_id = (best.get("acf") or {}).get("subtitle")
    try:
        async with session.get(
            f"{GOAT_API}/media/{media_id}",
            params={"_fields": "source_url"},
            timeout=20,
        ) as r:
            if r.status != 200:
                return None, None
            media = await r.json()
    except Exception as e:
        logger.exception(e)
        return None, None
    url = media.get("source_url") or ""
    if not url.lower().endswith(".srt"):
        return None, None
    try:
        async with session.get(url, timeout=30) as r:
            if r.status != 200:
                return None, None
            content = await r.read()
    except Exception as e:
        logger.exception(e)
        return None, None
    raw_title = (best.get("title") or {}).get("rendered", "")
    title = re.sub(r"<[^>]+>", "", raw_title).strip() or best.get("slug", imdb_id)
    fname = re.sub(r'[\\/:*?"<>|]', "_", title).strip() or f"{imdb_id}_ml.srt"
    if not fname.lower().endswith(".srt"):
        fname += ".srt"
    return content, fname


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
        if lang == "ml":
            # Team GOAT first — better Malayalam subs, no API key needed.
            content, fname = await _fetch_goat(session, imdb_id)
            if content:
                bio = io.BytesIO(content)
                bio.name = fname
                try:
                    await client.send_document(
                        chat_id=query.message.chat.id,
                        document=bio,
                        caption=f"📝 <b>Subtitles</b> (Malayalam)\n<code>{bio.name}</code>\n© Team GOAT",
                    )
                    await query.message.delete()
                except Exception as e:
                    logger.exception(e)
                    await query.message.edit_text(
                        "⚠️ <b>Failed to send the file.</b>"
                    )
                return
            # else: fall through to OpenSubtitles
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
