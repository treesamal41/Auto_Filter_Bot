"""
IMDb search for Carla — /imdb <movie name>.
Shows complete movie details (proIMDb style) with poster,
plus a Subtitles button wired to the subtitle plugin.
"""

import logging

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils import get_poster

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def _clean(value, default="N/A"):
    if not value or str(value).strip() in ("N/A", "None", ""):
        return default
    return str(value).strip()


@Client.on_message(filters.command("imdb") & filters.incoming)
async def imdb_search(client, message):
    if len(message.command) < 2:
        return await message.reply_text(
            "🔎 <b>Usage:</b> <code>/imdb movie name</code>\n"
            "Example: <code>/imdb Avatar 2009</code>"
        )
    query = message.text.split(None, 1)[1].strip()
    wait = await message.reply_text("🔎 <b>Searching IMDb...</b>")

    try:
        data = await get_poster(query)
    except Exception as e:
        logger.exception(e)
        data = None
    if not data:
        return await wait.edit_text(
            f"😕 <b>No IMDb results for:</b> <code>{query}</code>"
        )

    title = _clean(data.get("title"))
    year = _clean(data.get("year"))
    rating = _clean(data.get("rating"))
    votes = _clean(data.get("votes"))
    imdb_id = _clean(data.get("imdb_id"))
    kind = _clean(data.get("kind")).title()
    runtime = _clean(data.get("runtime"))
    release = _clean(data.get("release_date"))
    langs = _clean(data.get("languages"))
    genres = _clean(data.get("genres"))
    plot = _clean(data.get("plot"), "No storyline available.")
    director = _clean(data.get("director"))
    writer = _clean(data.get("writer"))
    cast = _clean(data.get("cast"))
    url = data.get("url") or f"https://www.imdb.com/title/{imdb_id}"

    text = (
        f"🎬 <b><a href=\"{url}\">{title} ({year})</a></b>\n\n"
        f"⭐ <b>Rating:</b> {rating}/10 <i>({votes} votes)</i>\n"
        f"🆔 <b>IMDb ID:</b> <code>{imdb_id}</code>\n"
        f"🎞 <b>Type:</b> {kind}\n"
        f"⏱ <b>Duration:</b> {runtime}\n"
        f"📅 <b>Release:</b> {release}\n"
        f"🗣 <b>Language:</b> {langs}\n"
        f"🎭 <b>Genre:</b> {genres}\n\n"
        f"📖 <b>Story:</b> <i>{plot}</i>\n\n"
        f"🎬 <b>Director:</b> {director}\n"
        f"✍️ <b>Writer:</b> {writer}\n"
        f"🌟 <b>Cast:</b> {cast}"
    )

    buttons = []
    if data.get("imdb_id"):
        buttons.append(
            [InlineKeyboardButton("📝 sᴜʙᴛɪᴛʟᴇs", callback_data=f"subdl#{data['imdb_id']}")]
        )
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    poster = data.get("poster")
    try:
        if poster:
            await message.reply_photo(
                photo=poster, caption=text, reply_markup=markup
            )
        else:
            await message.reply_text(text, reply_markup=markup)
        await wait.delete()
    except Exception as e:
        logger.exception(e)
        try:
            await wait.edit_text(text, reply_markup=markup)
        except Exception:
            pass
