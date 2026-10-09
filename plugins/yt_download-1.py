import asyncio
import json
import logging
import os
import re
import urllib.parse
import urllib.request

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

YT_RE = r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/\S+"
MAX_SIZE = 48 * 1024 * 1024  # Telegram bot limit is 50MB

# DESI API mirrors (unofficial third-party YouTube API). Primary first, fallback second.
API_BASES = [
    "https://samra-youtube-api.antideploy.com",
    "https://samra-youtube-api.krishnalucky193.workers.dev",
]

# message.id -> url (callback_data has 64-byte limit, can't fit full URL)
_pending = {}


def _api_json(base, endpoint, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{base}{endpoint}?{qs}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def _resolve(url, mode):
    """Return (direct_file_url, filename) via DESI API, trying mirrors."""
    endpoint = "/mp3" if mode == "audio" else "/mp4"
    params = {"url": url, "quality": "128" if mode == "audio" else "720"}
    last_err = None
    for base in API_BASES:
        try:
            data = _api_json(base, endpoint, params)
            if data.get("status") and data.get("url"):
                return data["url"], data.get("filename", "media")
            last_err = f"API returned no url: {str(data)[:120]}"
        except Exception as e:
            last_err = str(e)
    raise RuntimeError(f"API failed: {last_err}")


def _download_file(file_url, filename):
    os.makedirs("/tmp/ytdl", exist_ok=True)
    safe = re.sub(r"[^\w\-. ]", "_", filename)[:80] or "media"
    path = os.path.join("/tmp/ytdl", safe)
    req = urllib.request.Request(file_url, headers={"User-Agent": "Mozilla/5.0"})
    total = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SIZE:
                f.close()
                os.remove(path)
                raise RuntimeError("too_big")
            f.write(chunk)
    return path


def _fetch(url, mode):
    qualities = ["720", "480", "360"] if mode == "video" else ["128"]
    last_err = None
    for q in qualities:
        endpoint = "/mp3" if mode == "audio" else "/mp4"
        for base in API_BASES:
            try:
                data = _api_json(base, endpoint, {"url": url, "quality": q})
                if data.get("status") and data.get("url"):
                    path = _download_file(data["url"], data.get("filename", "media"))
                    title = data.get("filename", "media")
                    return path, title
            except RuntimeError as e:
                if str(e) == "too_big":
                    last_err = "too_big"
                    break  # try lower quality
                last_err = str(e)
            except Exception as e:
                last_err = str(e)
        if last_err == "too_big":
            continue
    raise RuntimeError(last_err or "download failed")


@Client.on_message(filters.regex(YT_RE) & filters.private, group=-1)
async def yt_link_handler(client, message):
    url = message.text.strip().split()[0]
    _pending[message.id] = url
    await message.reply(
        "YouTube link kitti. Ethu venam?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎵 Song", callback_data=f"ytdl:audio:{message.id}"),
            InlineKeyboardButton("🎬 Video", callback_data=f"ytdl:video:{message.id}"),
        ]]),
    )
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^ytdl:"))
async def yt_download_cb(client, query):
    _, mode, mid = query.data.split(":")
    url = _pending.pop(int(mid), None)
    if not url:
        return await query.answer("Link expired. Veendum ayakku.", show_alert=True)
    await query.answer("Downloading...")
    status = await query.message.edit("⏳ Downloading...")
    try:
        path, title = await asyncio.to_thread(_fetch, url, mode)
        if mode == "audio":
            await query.message.reply_audio(path, title=title, caption=f"🎵 {title}")
        else:
            await query.message.reply_video(path, caption=f"🎬 {title}")
        os.remove(path)
        await status.delete()
    except RuntimeError as e:
        if str(e) == "too_big":
            await status.edit("⚠️ File 48MB-il kooduthal aanu — Telegram limit.")
        else:
            logger.warning(f"YT download failed: {e}")
            await status.edit("⚠️ Download failed. Veendum try cheyyu.")
    except Exception as e:
        logger.warning(f"YT download failed: {e}")
        await status.edit("⚠️ Download failed. Veendum try cheyyu.")
