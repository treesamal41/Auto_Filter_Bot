"""
IMDb search for Carla — /imdb <movie name>.
Shows complete movie details (proIMDb style) with poster,
plus a Subtitles button wired to the subtitle plugin.
"""

import html
import logging
import re

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils import get_poster

try:
    from info import TMDB_API_KEY
except ImportError:
    from os import environ
    TMDB_API_KEY = environ.get("TMDB_API_KEY", "")

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)


def _clean(value, default="N/A"):
    if not value or str(value).strip() in ("N/A", "None", ""):
        return default
    return str(value).strip()


def _tag(name):
    """Blue tappable hashtag, proIMDb style: <a>#Horror</a>."""
    from urllib.parse import quote_plus
    label = "#" + re.sub(r"\s+", "_", str(name).strip())
    q = quote_plus(str(name).strip())
    return f"<a href=\"https://www.imdb.com/find?q={q}\">{label}</a>"


_LANG_NAMES = {
    "en": "English", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil",
    "te": "Telugu", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "tr": "Turkish", "nl": "Dutch",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "pl": "Polish", "uk": "Ukrainian", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay",
}


def _clean_names(value):
    """Strip role suffixes like ' (Cast)' / ' (Director)'."""
    return ", ".join(
        re.sub(r"\s*\([^)]*\)\s*", "", n).strip()
        for n in str(value).split(",")
        if n.strip()
    )


def _clean_cert(certs):
    """'Argentina:AR, United States:R' -> prefer US rating."""
    parts = [p.strip() for p in str(certs).split(",") if p.strip()]
    for p in parts:
        if ":" in p:
            country, rating = p.split(":", 1)
            if country.strip().lower() in ("united states", "usa", "us"):
                return rating.strip()
    for p in parts:
        if ":" in p:
            return p.split(":", 1)[1].strip()
    return parts[0] if parts else "N/A"


def _clean_runtime(rt):
    m = re.search(r"(\d+)", str(rt))
    if not m:
        return str(rt)
    mins = int(m.group(1))
    h, mm = divmod(mins, 60)
    return f"{h}h {mm}min | {mins} min" if h else f"{mins} min"


def _clean_langs(langs):
    out = []
    for l in str(langs).split(","):
        l = l.strip()
        if l:
            out.append(_LANG_NAMES.get(l.lower(), l))
    return ", ".join(out) if out else "N/A"


def _fb(text):
    """Mathematical bold label style: TITLE -> 𝐓𝐈𝐓𝐋𝐄."""
    out = []
    for ch in text:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(0x1D400 + o - 65))
        elif 97 <= o <= 122:
            out.append(chr(0x1D41A + o - 97))
        elif 48 <= o <= 57:
            out.append(chr(0x1D7CE + o - 48))
        else:
            out.append(ch)
    return "".join(out)


_GENRE_EMOJI = {
    "horror": "🧟", "romance": "❤️", "thriller": "😱", "action": "💥",
    "comedy": "😂", "drama": "🎭", "sci-fi": "🚀", "science fiction": "🚀",
    "adventure": "🗺️", "crime": "🔫", "mystery": "🔍", "fantasy": "🧙",
    "animation": "🎨", "family": "👪", "war": "⚔️", "history": "📜",
    "music": "🎵", "musical": "🎶", "western": "🤠", "sport": "⚽",
    "biography": "📖", "documentary": "🎥",
}


def _clean_date(d):
    if hasattr(d, "day"):
        return f"{d.day} / {d.month} / {d.year}"
    s = str(d).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(3))} / {int(m.group(2))} / {m.group(1)}"
    return s if s not in ("N/A", "None", "") else "N/A"


async def _streaming(session, imdb_id):
    """Streaming providers via TMDB (India, else US). Returns 'A | B' or None."""
    if not TMDB_API_KEY:
        return None
    try:
        async with session.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            timeout=15,
        ) as r:
            if r.status != 200:
                return None
            found = await r.json()
        movie_results = found.get("movie_results") or []
        tv_results = found.get("tv_results") or []
        if movie_results:
            media, mtype = movie_results[0], "movie"
        elif tv_results:
            media, mtype = tv_results[0], "tv"
        else:
            return None
        tmdb_id = media.get("id")
        async with session.get(
            f"https://api.themoviedb.org/3/{mtype}/{tmdb_id}/watch/providers",
            params={"api_key": TMDB_API_KEY},
            timeout=15,
        ) as r:
            if r.status != 200:
                return None
            prov = await r.json()
        results = prov.get("results") or {}
        region = results.get("IN") or results.get("US") or {}
        names = []
        for key in ("flatrate", "buy", "rent"):
            for p in region.get(key) or []:
                n = p.get("provider_name")
                if n and n not in names:
                    names.append(n)
        return " | ".join(names[:6]) if names else None
    except Exception as e:
        logger.exception(e)
        return None


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
    runtime = _clean_runtime(runtime) if runtime != "N/A" else runtime
    release = _clean(data.get("release_date"))
    release = _clean_date(data.get("release_date")) if release != "N/A" else release
    countries = _clean(data.get("countries"))
    langs = _clean(data.get("languages"))
    langs = _clean_langs(langs) if langs != "N/A" else langs
    genres = _clean(data.get("genres"))
    plot = _clean(data.get("plot"), "No storyline available.")
    if len(plot) > 450:
        plot = plot[:450].rsplit(" ", 1)[0] + "..."
    director = _clean(data.get("director"))
    director = _clean_names(director) if director != "N/A" else director
    writer = _clean(data.get("writer"))
    writer = _clean_names(writer) if writer != "N/A" else writer
    cast = _clean(data.get("cast"))
    cast = _clean_names(cast) if cast != "N/A" else cast
    aka = _clean(data.get("aka"), "")
    cert = _clean(data.get("certificates"), "")
    cert = _clean_cert(cert) if cert != "N/A" else ""
    url = data.get("url") or f"https://www.imdb.com/title/{imdb_id}"

    async with aiohttp.ClientSession() as session:
        streaming = await _streaming(session, imdb_id)

    lang_tags = " ".join(_tag(l) for l in langs.split(",") if l.strip()) if langs != "N/A" else "N/A"
    country_tag = " ".join(_tag(c) for c in countries.split(",")[:1] if c.strip())

    genre_bits = []
    for g in str(genres).split(","):
        g = g.strip()
        if not g or g == "N/A":
            continue
        emo = _GENRE_EMOJI.get(g.lower(), "")
        genre_bits.append(f"{emo} {_tag(g)}".strip())
    genre_line = " ".join(genre_bits) if genre_bits else "N/A"

    def _nlink(name):
        from urllib.parse import quote_plus
        name = name.strip()
        return f"<a href=\"https://www.imdb.com/find?q={quote_plus(name)}&s=nm\">{name}</a>"

    def _nlinks(value):
        return ", ".join(_nlink(n) for n in str(value).split(",") if n.strip())

    text = (
        f"🎪 {_fb('TITLE')}: <a href=\"{url}\">{title} ({year})</a>\n"
        + (f"ALSO KNOWN AS {aka}\n" if aka != "N/A" else "")
        + f"🏆 {_fb('USER RATINGS')}: {rating} / 10" + (f" | {cert}" if cert else "") + "\n"
        f"({rating} based on {votes} user ratings)\n"
        f"🌠 {_fb('IMDB ID')}: {imdb_id}\n"
        f"🪬 {_fb('TITLE TYPE')}: {kind}\n"
        f"🕰 {_fb('DURATION')}: {runtime}\n"
        + (f"🍿 {_fb('STREAMING ON')}: {streaming}\n" if streaming else "")
        + f"{_fb('RELEASE DATE')}: {release}"
        + (f" ({countries.split(',')[0].strip()}) {country_tag}" if countries != "N/A" else "")
        + f"\n💬 {_fb('LANGUAGE')}: {lang_tags}\n"
        + f"📼 {_fb('GENRE')}: {genre_line}\n\n"
        f"📝 {_fb('STORY LINE')}: <code>{html.escape(plot)}</code>\n\n"
        f"🎥 {_fb('DIRECTOR')}: {_nlinks(director)}\n"
        f"🎞 {_fb('WRITER')}: {_nlinks(writer)}\n"
        f"🚥 {_fb('ACTORS')}: {_nlinks(cast)}\n\n"
        f"⚙️ <a href=\"https://www.imdb.com/title/{imdb_id}/technical\">Check technical specifications</a>"
    )

    buttons = []
    trailer_url = f"https://www.youtube.com/results?search_query={title} {year} trailer".replace(" ", "+")
    buttons.append([InlineKeyboardButton("🎬 Trailer", url=trailer_url)])
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
