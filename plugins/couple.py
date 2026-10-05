"""
Couple plugin for Carla (private chat only).

Pair two partners, then:
  /couple     - main menu (Miss You button, countdown, memory, location)
  /paircode   - generate pairing code
  /pair CODE  - link with partner
  /unpair     - unlink
  /countdown  - time since proposal (14 Feb 2024) + days to next anniversary
  /memory     - random photo from shared album
  /cphotos    - album photo count
  /couplemode couple|eod - (admins) private photos -> album or EOD tally
  Send a photo    - saved to the couple's shared album
  Share location  - forwarded to partner (live location keeps updating)

Data lives in Mongo (couple_pairs / couple_codes / couple_photos),
so it survives dyno restarts.
"""

import logging
import secrets
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from database.users_chats_db import db

logger = logging.getLogger(__name__)

PROPOSAL_DATE = datetime(2024, 2, 14)

# Only these two users can use the couple features (Amal + Treesa).
COUPLE_IDS = {1294939227, 8155805976}

pairs_col = db.db.couple_pairs
codes_col = db.db.couple_codes
photos_col = db.db.couple_photos
modes_col = db.db.couple_modes  # {"_id": str(user_id), "mode": "couple"|"eod"}

try:
    from info import ADMINS as _ADMINS
except Exception:
    _ADMINS = []

_private = filters.private
_couple_only = filters.private & filters.user(list(COUPLE_IDS))


def _pair_key(a: int, b: int) -> str:
    x, y = sorted((a, b))
    return f"{x}_{y}"


async def _get_partner(user_id: int):
    doc = await pairs_col.find_one({"_id": str(user_id)})
    return doc["partner"] if doc else None


def _countdown_text(now=None) -> str:
    now = now or datetime.now()
    delta = now - PROPOSAL_DATE
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    seconds = delta.seconds % 60
    anni = datetime(now.year, 2, 14)
    if anni <= now:
        anni = datetime(now.year + 1, 2, 14)
    to_go = (anni - now).days
    return (
        "Since proposal (14 Feb 2024):\n"
        f"{days} days, {hours} hrs, {minutes} min, {seconds} sec\n\n"
        f"Next anniversary: {to_go} days to go"
    )


def _menu():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Miss You", callback_data="couple_missyou")],
            [InlineKeyboardButton("Countdown", callback_data="couple_countdown")],
            [InlineKeyboardButton("Random Memory", callback_data="couple_memory")],
            [InlineKeyboardButton("Share My Location", callback_data="couple_shareloc")],
        ]
    )


@Client.on_message(filters.command("couple") & _couple_only)
async def couple_menu(client, message):
    partner = await _get_partner(message.from_user.id)
    if partner:
        await message.reply_text(
            f"Hey {message.from_user.first_name}. Paired and ready.",
            reply_markup=_menu(),
        )
    else:
        await message.reply_text(
            "Pair with your partner first:\n"
            "1. One of you sends /paircode\n"
            "2. The other sends /pair <code>"
        )


@Client.on_message(filters.command("paircode") & _couple_only)
async def couple_paircode(client, message):
    user_id = message.from_user.id
    if await _get_partner(user_id):
        await message.reply_text("Already paired. /unpair to unlink first.")
        return
    code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))
    await codes_col.update_one(
        {"_id": code}, {"$set": {"user": user_id}}, upsert=True
    )
    await message.reply_text(
        f"Your pairing code: <code>{code}</code>\n"
        f"Partner should send: /pair {code}",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("pair") & _couple_only)
async def couple_pair(client, message):
    user_id = message.from_user.id
    if await _get_partner(user_id):
        await message.reply_text("Already paired. /unpair to unlink first.")
        return
    if len(message.command) < 2:
        await message.reply_text("Usage: /pair <code>")
        return
    code = message.command[1].strip().upper()
    doc = await codes_col.find_one_and_delete({"_id": code})
    if not doc:
        await message.reply_text("Invalid or expired code.")
        return
    other_id = doc["user"]
    if other_id == user_id:
        await message.reply_text("That is your own code.")
        return
    await pairs_col.update_one(
        {"_id": str(user_id)}, {"$set": {"partner": other_id}}, upsert=True
    )
    await pairs_col.update_one(
        {"_id": str(other_id)}, {"$set": {"partner": user_id}}, upsert=True
    )
    await message.reply_text(
        "Paired! Send a photo to save it to your shared album.",
        reply_markup=_menu(),
    )
    try:
        await client.send_message(
            other_id,
            f"{message.from_user.first_name} paired with you! "
            "Send a photo to save it to your shared album.",
            reply_markup=_menu(),
        )
    except Exception:
        pass


@Client.on_message(filters.command("unpair") & _couple_only)
async def couple_unpair(client, message):
    user_id = message.from_user.id
    partner = await _get_partner(user_id)
    if not partner:
        await message.reply_text("Not paired.")
        return
    await pairs_col.delete_one({"_id": str(user_id)})
    await pairs_col.delete_one({"_id": str(partner)})
    await message.reply_text("Unpaired.")
    try:
        await client.send_message(partner, "Your partner unpaired the bot.")
    except Exception:
        pass


@Client.on_message(filters.command("countdown") & _couple_only)
async def couple_countdown(client, message):
    await message.reply_text(_countdown_text())


@Client.on_message(filters.command("memory") & _couple_only)
async def couple_memory(client, message):
    user_id = message.from_user.id
    partner = await _get_partner(user_id)
    if not partner:
        await message.reply_text("Pair first with /paircode.")
        return
    await _send_random_memory(client, message, _pair_key(user_id, partner))


@Client.on_message(filters.command("cphotos") & _couple_only)
async def couple_photos(client, message):
    user_id = message.from_user.id
    partner = await _get_partner(user_id)
    if not partner:
        await message.reply_text("Pair first with /paircode.")
        return
    n = await photos_col.count_documents({"pair": _pair_key(user_id, partner)})
    await message.reply_text(f"{n} photos in your shared album.")


async def _send_random_memory(client, message, pair_key):
    import random

    items = await photos_col.find({"pair": pair_key}).to_list(length=500)
    if not items:
        await message.reply_text("No photos yet. Send one to start the album.")
        return
    item = random.choice(items)
    cap = item.get("caption") or f"Shared by {item.get('from_name', 'partner')}"
    await message.reply_photo(item["file_id"], caption=cap)


@Client.on_callback_query(filters.regex(r"^couple_"))
async def couple_callback(client, query):
    user_id = query.from_user.id
    if user_id not in COUPLE_IDS:
        await query.answer("Not for you.", show_alert=True)
        return
    partner = await _get_partner(user_id)
    action = query.data.split("_", 1)[1]

    if action == "missyou":
        await query.answer()
        if not partner:
            await query.message.reply_text("Pair first with /paircode.")
            return
        try:
            await client.send_message(
                partner, f"{query.from_user.first_name} misses you!"
            )
            await query.message.reply_text("Sent.")
        except Exception:
            await query.message.reply_text("Could not reach your partner.")
    elif action == "countdown":
        await query.answer()
        await query.message.reply_text(_countdown_text())
    elif action == "memory":
        await query.answer()
        if not partner:
            await query.message.reply_text("Pair first with /paircode.")
            return
        await _send_random_memory(client, query.message, _pair_key(user_id, partner))
    elif action == "shareloc":
        await query.answer()
        if not partner:
            await query.message.reply_text("Pair first with /paircode.")
            return
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("Send my location", request_location=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await query.message.reply_text(
            "Tap the button below to share your location with your partner.",
            reply_markup=kb,
        )


@Client.on_message(filters.command("couplemode") & _couple_only)
async def couple_mode(client, message):
    """Admins: /couplemode couple -> private photos go to album.
    /couplemode eod -> private photos go to EOD book tally (default)."""
    user_id = message.from_user.id
    arg = message.command[1].lower() if len(message.command) > 1 else ""
    if arg in ("couple", "eod"):
        await modes_col.update_one(
            {"_id": str(user_id)}, {"$set": {"mode": arg}}, upsert=True
        )
        await message.reply_text(
            "Photo mode: "
            + ("couple album" if arg == "couple" else "EOD book tally")
        )
    else:
        cur = await modes_col.find_one({"_id": str(user_id)})
        cur_mode = cur.get("mode") if cur else "eod"
        await message.reply_text(
            f"Current photo mode: {cur_mode}\n"
            "Usage: /couplemode couple  or  /couplemode eod"
        )


@Client.on_message(filters.photo & _couple_only)
async def couple_photo(client, message):
    user_id = message.from_user.id
    partner = await _get_partner(user_id)
    if not partner:
        return
    # Admins: EOD book tally yum private photos use cheyyunnu;
    # couple mode onenkil mathram album ilekku save cheyyu.
    if user_id in _ADMINS:
        mode = await modes_col.find_one({"_id": str(user_id)})
        if not mode or mode.get("mode") != "couple":
            return  # EOD handler will take it
    # skip photos that belong to other features (none currently use private photos)
    photo = message.photo
    # pyrogram: message.photo is a single PhotoSize (largest)
    file_id = photo.file_id if hasattr(photo, "file_id") else photo[-1].file_id
    await photos_col.insert_one(
        {
            "pair": _pair_key(user_id, partner),
            "file_id": file_id,
            "caption": message.caption or "",
            "from_name": message.from_user.first_name,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    n = await photos_col.count_documents({"pair": _pair_key(user_id, partner)})
    await message.reply_text(f"Saved to your shared album ({n} photos).")


@Client.on_message(filters.location & _couple_only)
async def couple_location(client, message):
    user_id = message.from_user.id
    partner = await _get_partner(user_id)
    if not partner:
        await message.reply_text(
            "Pair first with /paircode.", reply_markup=ReplyKeyboardRemove()
        )
        return
    try:
        # Forwarding keeps live location updating for the partner.
        await client.forward_messages(
            chat_id=partner,
            from_chat_id=message.chat.id,
            message_ids=message.id,
        )
        await message.reply_text(
            "Location shared with your partner.",
            reply_markup=ReplyKeyboardRemove(),
        )
    except Exception:
        await message.reply_text(
            "Could not reach your partner.", reply_markup=ReplyKeyboardRemove()
        )
