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
SOCIAL_RE = r"(https?://)?(www\.)?(instagram\.com|facebook\.com|fb\.watch|tiktok\.com|dailymotion\.com|dai\.ly)/\S+"
SOCIAL_API = "https://samra-insta-fb-api.krishnalucky193.workers.dev/"
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


def _fetch(url, mode, start_quality=None):
    if mode == "video":
        all_q = ["2160", "1440", "1080", "720", "480", "360"]
        qualities = all_q[all_q.index(start_quality):] if start_quality in all_q else all_q
    else:
        all_q = ["320", "192", "128"]
        qualities = all_q[all_q.index(start_quality):] if start_quality in all_q else all_q
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


def _fetch_social(url):
    """Resolve Instagram/FB/TikTok/Dailymotion link -> (direct mp4 url, title)."""
    qs = urllib.parse.urlencode({"url": url})
    req = urllib.request.Request(SOCIAL_API + "?" + qs,
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("status"):
        raise RuntimeError(data.get("error", "API failed")[:120])
    title = data.get("title", "video")
    for dl in data.get("downloads", []):
        if dl.get("type") == "video" and dl.get("url"):
            return dl["url"], title
    raise RuntimeError("no video found")


@Client.on_message(filters.regex(SOCIAL_RE) & filters.private, group=-1)
async def social_link_handler(client, message):
    url = message.text.strip().split()[0]
    status = await message.reply("⏳ Downloading...")
    try:
        dl_url, title = await asyncio.to_thread(_fetch_social, url)
        safe = re.sub(r"[^\w\-. ]", "_", str(title))[:60] or "video"
        path = await asyncio.to_thread(_download_file, dl_url, safe + ".mp4")
        await message.reply_video(path, caption="🎬")
        os.remove(path)
        await status.delete()
    except RuntimeError as e:
        if str(e) == "too_big":
            await status.edit("⚠️ File 48MB-il kooduthal aanu — Telegram limit.")
        else:
            logger.warning(f"Social download failed: {e}")
            await status.edit("⚠️ Download failed. Veendum try cheyyu.")
    except Exception as e:
        logger.warning(f"Social download failed: {e}")
        await status.edit("⚠️ Download failed. Veendum try cheyyu.")
    raise StopPropagation


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
    parts = query.data.split(":")
    action = parts[1]

    if action == "video":
        # show quality options
        mid = parts[2]
        if int(mid) not in _pending:
            return await query.answer("Link expired. Veendum ayakku.", show_alert=True)
        await query.message.edit(
            "Quality select cheyyu:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("360p", callback_data=f"ytdl:q:360:{mid}"),
                    InlineKeyboardButton("480p", callback_data=f"ytdl:q:480:{mid}"),
                    InlineKeyboardButton("720p", callback_data=f"ytdl:q:720:{mid}"),
                ],
                [
                    InlineKeyboardButton("1080p", callback_data=f"ytdl:q:1080:{mid}"),
                    InlineKeyboardButton("1440p", callback_data=f"ytdl:q:1440:{mid}"),
                    InlineKeyboardButton("4K", callback_data=f"ytdl:q:2160:{mid}"),
                ],
            ]),
        )
        return await query.answer()

    if action == "audio":
        # show audio quality options
        mid = parts[2]
        if int(mid) not in _pending:
            return await query.answer("Link expired. Veendum ayakku.", show_alert=True)
        await query.message.edit(
            "Audio quality select cheyyu:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("128 kbps", callback_data=f"ytdl:a:128:{mid}"),
                InlineKeyboardButton("192 kbps", callback_data=f"ytdl:a:192:{mid}"),
                InlineKeyboardButton("320 kbps", callback_data=f"ytdl:a:320:{mid}"),
            ]]),
        )
        return await query.answer()

    if action == "q":
        quality, mid = parts[2], int(parts[3])
        mode = "video"
    else:  # a = audio quality
        quality, mid, mode = parts[2], int(parts[3]), "audio"

    url = _pending.pop(mid, None)
    if not url:
        return await query.answer("Link expired. Veendum ayakku.", show_alert=True)
    await query.answer("Downloading...")
    status = await query.message.edit("⏳ Downloading...")
    try:
        path, title = await asyncio.to_thread(_fetch, url, mode, quality)
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
