"""Per-group keyword filters ported from Unlimited-Filter-Bot.

Commands (group admins / AUTH_USERS):
  /add <keyword> <text>      - add filter (or reply to a message with /add <keyword>)
  /del <keyword>             - delete a filter
  /delall                    - delete all filters of the group (owner only)
  /viewfilters               - list filters
  /connect [/connect <id>]    - connect bot PM to a group
  /disconnect                - disconnect from group
  /connections               - list connected groups (PM only)

Button syntax inside filter text:
  [Label](buttonurl:https://example.com)
  [Label](buttonalert:popup text here)
  Add :same at the end to put the button on the same row.
"""
import ast
import asyncio
import io
import json
import logging
import re

import motor.motor_asyncio
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from info import ADMINS, AUTH_USERS, DATABASE_URI, DATABASE_NAME
from utils import is_check_admin

logger = logging.getLogger(__name__)

_mclient = motor.motor_asyncio.AsyncIOMotorClient(DATABASE_URI)
_pgfilters = _mclient[DATABASE_NAME].pergroup_filters
_pgconns = _mclient[DATABASE_NAME].pgf_connections

# ------------------------------------------------------------------ helpers

BTN_URL_REGEX = re.compile(
    r"(\[([^\[]+?)\]\((buttonurl|buttonalert):(?:/{0,2})(.+?)(:same)?\))"
)
SMART_OPEN = '\u201c'
SMART_CLOSE = '\u201d'
START_CHAR = ("'", '"', SMART_OPEN)


def _remove_escapes(text: str) -> str:
    res, escaped = "", False
    for ch in text:
        if escaped:
            res += ch
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            res += ch
    return res


def split_quotes(text: str):
    if any(text.startswith(c) for c in START_CHAR):
        counter = 1
        while counter < len(text):
            if text[counter] == "\\":
                counter += 1
            elif text[counter] == text[0] or (text[0] == SMART_OPEN and text[counter] == SMART_CLOSE):
                break
            counter += 1
        else:
            return text.split(None, 1)
        key = _remove_escapes(text[1:counter].strip())
        rest = text[counter + 1:].strip()
        if not key:
            key = text[0] + text[0]
        return list(filter(None, [key, rest]))
    return text.split(None, 1)


def parser(text: str, keyword: str):
    """Parse button markup. Returns (note_text, buttons, alerts).
    buttons: list of rows; each button is {'text':..,'url':..} or {'text':..,'alert':idx}"""
    if "buttonalert" in text:
        text = text.replace("\n", "\\n").replace("\t", "\\t")
    buttons, alerts, note_data = [], [], ""
    prev, i = 0, 0
    for match in BTN_URL_REGEX.finditer(text):
        n_escapes, to_check = 0, match.start(1) - 1
        while to_check > 0 and text[to_check] == "\\":
            n_escapes += 1
            to_check -= 1
        if n_escapes % 2 == 0:
            note_data += text[prev:match.start(1)]
            prev = match.end(1)
            label = match.group(2)
            same_row = bool(match.group(5)) and buttons
            if match.group(3) == "buttonalert":
                btn = {"text": label, "alert": i}
                alerts.append(match.group(4))
                i += 1
            else:
                btn = {"text": label, "url": match.group(4).replace(" ", "")}
            if same_row:
                buttons[-1].append(btn)
            else:
                buttons.append([btn])
        else:
            note_data += text[prev:to_check]
            prev = match.start(1) - 1
    note_data += text[prev:]
    return note_data, buttons, alerts


def build_markup(buttons, keyword):
    rows = []
    for row in buttons:
        brow = []
        for b in row:
            if "url" in b:
                brow.append(InlineKeyboardButton(b["text"], url=b["url"]))
            elif "alert" in b:
                brow.append(InlineKeyboardButton(
                    b["text"], callback_data=f"pgf_alert:{b['alert']}:{keyword}"))
        if brow:
            rows.append(brow)
    return InlineKeyboardMarkup(rows) if rows else None


# ------------------------------------------------------------------ db

async def db_add_filter(grp_id, keyword, reply, buttons, file_id, alerts):
    await _pgfilters.update_one(
        {"grp_id": str(grp_id), "keyword": keyword},
        {"$set": {"reply": reply or "", "btn": json.dumps(buttons or []),
                  "file": file_id or "None", "alert": json.dumps(alerts or [])}},
        upsert=True,
    )


async def db_find_filter(grp_id, keyword):
    doc = await _pgfilters.find_one({"grp_id": str(grp_id), "keyword": keyword})
    if not doc:
        return None
    return (doc.get("reply", ""), json.loads(doc.get("btn") or "[]"),
            json.loads(doc.get("alert") or "[]"), doc.get("file") or "None")


async def db_get_keywords(grp_id):
    cur = _pgfilters.find({"grp_id": str(grp_id)})
    return [d["keyword"] async for d in cur]


async def db_del_filter(grp_id, keyword):
    res = await _pgfilters.delete_one({"grp_id": str(grp_id), "keyword": keyword})
    return res.deleted_count > 0


async def db_del_all(grp_id):
    res = await _pgfilters.delete_many({"grp_id": str(grp_id)})
    return res.deleted_count


async def db_count(grp_id):
    return await _pgfilters.count_documents({"grp_id": str(grp_id)})


# connections ---------------------------------------------------------------
async def add_connection(group_id, user_id):
    user_id, group_id = str(user_id), str(group_id)
    doc = await _pgconns.find_one({"_id": user_id})
    if doc and group_id in [g["group_id"] for g in doc.get("group_details", [])]:
        return False
    if not doc:
        await _pgconns.insert_one(
            {"_id": user_id, "group_details": [{"group_id": group_id}],
             "active_group": group_id})
    else:
        await _pgconns.update_one(
            {"_id": user_id},
            {"$push": {"group_details": {"group_id": group_id}},
             "$set": {"active_group": group_id}})
    return True


async def active_connection(user_id):
    doc = await _pgconns.find_one({"_id": str(user_id)})
    if doc and doc.get("active_group"):
        return int(doc["active_group"])
    return None


async def all_connections(user_id):
    doc = await _pgconns.find_one({"_id": str(user_id)})
    if doc:
        return [g["group_id"] for g in doc.get("group_details", [])]
    return None


async def if_active(user_id, group_id):
    doc = await _pgconns.find_one({"_id": str(user_id)})
    return bool(doc) and doc.get("active_group") == str(group_id)


async def make_active(user_id, group_id):
    res = await _pgconns.update_one({"_id": str(user_id)},
                                    {"$set": {"active_group": str(group_id)}})
    return res.modified_count > 0


async def make_inactive(user_id):
    res = await _pgconns.update_one({"_id": str(user_id)},
                                    {"$set": {"active_group": None}})
    return res.modified_count > 0


async def delete_connection(user_id, group_id):
    user_id, group_id = str(user_id), str(group_id)
    res = await _pgconns.update_one({"_id": user_id},
                                    {"$pull": {"group_details": {"group_id": group_id}}})
    if res.modified_count == 0:
        return False
    doc = await _pgconns.find_one({"_id": user_id})
    remaining = [g["group_id"] for g in doc.get("group_details", [])]
    new_active = remaining[-1] if remaining else None
    if doc.get("active_group") == group_id or not remaining:
        await _pgconns.update_one({"_id": user_id},
                                  {"$set": {"active_group": new_active}})
    return True


# ------------------------------------------------------------------ shared

def _auth_ids():
    return {str(a) for a in (AUTH_USERS or [])}


async def _is_grp_admin(client, grp_id, user_id):
    if str(user_id) in _auth_ids():
        return True
    try:
        return await is_check_admin(client, grp_id, user_id)
    except Exception:
        return False


async def _target_group(client, message):
    """Return (grp_id, title) or (None, error_text)."""
    if message.chat.type == enums.ChatType.PRIVATE:
        grp_id = await active_connection(str(message.from_user.id))
        if not grp_id:
            return None, "Group connect cheythittilla. /connect use cheyyu."
        try:
            chat = await client.get_chat(grp_id)
            return grp_id, chat.title
        except Exception:
            return None, "Bot aa group-il illa."
    return message.chat.id, message.chat.title


def _media_file_id(msg):
    for attr in ("photo", "video", "document", "audio", "animation"):
        m = getattr(msg, attr, None)
        if m:
            return m.file_id
    return None


def _markup_to_buttons(markup):
    """Convert existing inline keyboard url-buttons to our dict format."""
    buttons = []
    try:
        for row in markup.inline_keyboard:
            brow = []
            for b in row:
                if b.url:
                    brow.append({"text": b.text, "url": b.url})
            if brow:
                buttons.append(brow)
    except Exception:
        pass
    return buttons


# ------------------------------------------------------------------ commands

@Client.on_message(filters.command("add") & filters.incoming)
async def addfilter(client, message):
    user_id = message.from_user.id if message.from_user else 0
    grp_id, title = await _target_group(client, message)
    if not grp_id:
        return await message.reply_text(title, quote=True)
    if not await _is_grp_admin(client, grp_id, user_id):
        return

    args = (message.text or "").split(None, 1)
    if len(args) < 2:
        return await message.reply_text("Use: `/add keyword` as reply, or `/add keyword text`", quote=True)

    extracted = split_quotes(args[1])
    keyword = extracted[0].lower()
    reply_msg = message.reply_to_message

    reply_text, buttons, alerts, file_id = "", [], [], None

    if reply_msg and reply_msg.reply_markup:
        file_id = _media_file_id(reply_msg)
        reply_text = reply_msg.caption or reply_msg.text or ""
        buttons = _markup_to_buttons(reply_msg.reply_markup)
    elif reply_msg:
        file_id = _media_file_id(reply_msg)
        src = reply_msg.caption if file_id else (reply_msg.text or "")
        reply_text, buttons, alerts = parser(src or "", keyword)
    elif len(extracted) >= 2:
        reply_text, buttons, alerts = parser(extracted[1], keyword)
        if not reply_text and not buttons:
            return await message.reply_text("Content kodukku!", quote=True)
    else:
        return await message.reply_text("Content kodukku! Message-nu reply aayi /add use cheyyu.", quote=True)

    await db_add_filter(grp_id, keyword, reply_text, buttons, file_id, alerts)
    await message.reply_text(f"Filter `{keyword}` added in **{title}**", quote=True)


@Client.on_message(filters.command("del") & filters.incoming)
async def delfilter(client, message):
    user_id = message.from_user.id if message.from_user else 0
    grp_id, title = await _target_group(client, message)
    if not grp_id:
        return await message.reply_text(title, quote=True)
    if not await _is_grp_admin(client, grp_id, user_id):
        return
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2:
        return await message.reply_text("Use: `/del keyword`", quote=True)
    if await db_del_filter(grp_id, parts[1].lower()):
        await message.reply_text(f"`{parts[1].lower()}` deleted.", quote=True)
    else:
        await message.reply_text("Aa keyword-il filter illa.", quote=True)


@Client.on_message(filters.command("viewfilters") & filters.incoming)
async def viewfilters(client, message):
    user_id = message.from_user.id if message.from_user else 0
    grp_id, title = await _target_group(client, message)
    if not grp_id:
        return await message.reply_text(title, quote=True)
    if not await _is_grp_admin(client, grp_id, user_id):
        return
    keywords = await db_get_keywords(grp_id)
    count = len(keywords)
    if not count:
        return await message.reply_text(f"**{title}**-il filters onnum illa.", quote=True)
    text = f"**{title}**-ile filters ({count}):\n\n" + "\n".join(f"× `{k}`" for k in keywords)
    if len(text) > 4000:
        f = io.BytesIO(text.encode())
        f.name = "filters.txt"
        return await message.reply_document(f, quote=True)
    await message.reply_text(text, quote=True)


@Client.on_message(filters.command("delall") & filters.incoming)
async def delall_ask(client, message):
    user_id = message.from_user.id if message.from_user else 0
    grp_id, title = await _target_group(client, message)
    if not grp_id:
        return await message.reply_text(title, quote=True)
    try:
        member = await client.get_chat_member(grp_id, user_id)
        is_owner = member.status == enums.ChatMemberStatus.OWNER
    except Exception:
        is_owner = False
    if not (is_owner or str(user_id) in _auth_ids()):
        return await message.reply_text("Group owner mathrame delall cheyyan pattu.", quote=True)
    await message.reply_text(
        f"**{title}**-ile ella filters-um delete cheyyatte?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("YES", callback_data="pgf_da_yes"),
             InlineKeyboardButton("CANCEL", callback_data="pgf_da_no")],
        ]),
        quote=True,
    )


# ---------------------------------------------------------- connect commands

@Client.on_message(filters.command("connect") & filters.incoming)
async def connect_grp(client, message):
    user_id = message.from_user.id if message.from_user else 0
    if message.chat.type == enums.ChatType.PRIVATE:
        parts = (message.text or "").split(None, 1)
        if len(parts) < 2:
            return await message.reply_text("Use: `/connect group_id`", quote=True)
        group_id = parts[1].strip()
    else:
        group_id = message.chat.id
    try:
        st = await client.get_chat_member(group_id, user_id)
        if st.status not in (enums.ChatMemberStatus.ADMINISTRATOR,
                             enums.ChatMemberStatus.OWNER) and str(user_id) not in _auth_ids():
            return await message.reply_text("Nee aa group-il admin alla!", quote=True)
    except Exception:
        return await message.reply_text("Invalid group! Bot aa group-il undo ennu nokku.", quote=True)
    try:
        me = await client.get_me()
        bst = await client.get_chat_member(group_id, me.id)
        if bst.status != enums.ChatMemberStatus.ADMINISTRATOR:
            return await message.reply_text("Bot-ne group-il admin aakku!", quote=True)
        chat = await client.get_chat(group_id)
    except Exception:
        return await message.reply_text("Group details edukkan pattilla.", quote=True)
    if await add_connection(chat.id, user_id):
        await message.reply_text(f"**{chat.title}**-ilekku connect cheythu! PM-il ninnu manage cheyyam.", quote=True)
    else:
        await message.reply_text("Already connected!", quote=True)


@Client.on_message(filters.command("disconnect") & filters.incoming)
async def disconnect_grp(client, message):
    user_id = message.from_user.id if message.from_user else 0
    if message.chat.type == enums.ChatType.PRIVATE:
        return await message.reply_text("/connections vazhi disconnect cheyyu.", quote=True)
    group_id = message.chat.id
    if not await _is_grp_admin(client, group_id, user_id):
        return
    if await delete_connection(user_id, group_id):
        await message.reply_text("Disconnected!", quote=True)
    else:
        await message.reply_text("Connect cheythittilla.", quote=True)


@Client.on_message(filters.command("connections") & filters.private & filters.incoming)
async def list_connections(client, message):
    user_id = message.from_user.id if message.from_user else 0
    group_ids = await all_connections(user_id)
    if not group_ids:
        return await message.reply_text("Connections onnum illa. /connect use cheyyu.", quote=True)
    buttons = []
    for gid in group_ids:
        try:
            chat = await client.get_chat(int(gid))
            label = chat.title + (" - ACTIVE" if await if_active(user_id, gid) else "")
            buttons.append([InlineKeyboardButton(label, callback_data=f"pgf_grp:{gid}")])
        except Exception:
            continue
    if not buttons:
        return await message.reply_text("Connections onnum illa.", quote=True)
    await message.reply_text("Connected groups:", reply_markup=InlineKeyboardMarkup(buttons), quote=True)


# ------------------------------------------------------------------ trigger

@Client.on_message(
    filters.group & filters.text & filters.incoming & ~filters.regex(r"^/"),
    group=-1,
)
async def pgf_trigger(client, message):
    group_id = message.chat.id
    name = message.text or ""
    try:
        keywords = await db_get_keywords(group_id)
    except Exception:
        return
    if not keywords:
        return
    for keyword in sorted(keywords, key=len, reverse=True):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            found = await db_find_filter(group_id, keyword)
            if not found:
                continue
            reply_text, buttons, alerts, file_id = found
            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")
            markup = build_markup(buttons, keyword)
            try:
                if file_id and file_id != "None":
                    await message.reply_cached_media(
                        file_id, caption=reply_text or "",
                        reply_markup=markup)
                else:
                    await message.reply_text(reply_text, reply_markup=markup,
                                             disable_web_page_preview=True)
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception:
                logger.exception("pgf trigger reply failed")
            raise StopPropagation


# ------------------------------------------------------------------ callbacks

@Client.on_callback_query(filters.regex(r"^pgf_"))
async def pgf_callbacks(client, query):
    data = query.data
    user_id = query.from_user.id

    if data.startswith("pgf_alert:"):
        _, idx, keyword = data.split(":", 2)
        grp_id = query.message.chat.id
        found = await db_find_filter(grp_id, keyword)
        if found:
            _, _, alerts, _ = found
            try:
                alert = alerts[int(idx)].replace("\\n", "\n").replace("\\t", "\t")
                await query.answer(alert, show_alert=True)
                raise StopPropagation
            except Exception:
                pass
        await query.answer()
        raise StopPropagation

    if data == "pgf_da_yes":
        grp_id, _ = await _target_group(client, query.message)
        try:
            member = await client.get_chat_member(grp_id, user_id)
            is_owner = member.status == enums.ChatMemberStatus.OWNER
        except Exception:
            is_owner = False
        if not (is_owner or str(user_id) in _auth_ids()):
            await query.answer("Group owner mathram!", show_alert=True)
            raise StopPropagation
        n = await db_del_all(grp_id)
        await query.message.edit_text(f"{n} filters deleted.")
        raise StopPropagation

    if data == "pgf_da_no":
        await query.message.delete()
        raise StopPropagation

    if data.startswith("pgf_grp:"):
        gid = data.split(":", 1)[1]
        try:
            chat = await client.get_chat(int(gid))
            title = chat.title
        except Exception:
            await query.answer("Group kittilla!", show_alert=True)
            raise StopPropagation
        act = await if_active(user_id, gid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("DISCONNECT" if act else "CONNECT",
                                  callback_data=f"pgf_con:{gid}" if not act else f"pgf_dis:{gid}"),
             InlineKeyboardButton("DELETE", callback_data=f"pgf_rm:{gid}")],
            [InlineKeyboardButton("BACK", callback_data="pgf_back")],
        ])
        await query.message.edit_text(f"**{title}**\nID: `{gid}`", reply_markup=kb)
        await query.answer()
        raise StopPropagation

    if data.startswith("pgf_con:"):
        gid = data.split(":", 1)[1]
        if await make_active(user_id, gid):
            await query.message.edit_text("Connected!")
        else:
            await query.answer("Failed!", show_alert=True)
        raise StopPropagation

    if data.startswith("pgf_dis:"):
        gid = data.split(":", 1)[1]
        if await make_inactive(user_id):
            await query.message.edit_text("Disconnected!")
        else:
            await query.answer("Failed!", show_alert=True)
        raise StopPropagation

    if data.startswith("pgf_rm:"):
        gid = data.split(":", 1)[1]
        if await delete_connection(user_id, gid):
            await query.message.edit_text("Connection deleted!")
        else:
            await query.answer("Failed!", show_alert=True)
        raise StopPropagation

    if data == "pgf_back":
        group_ids = await all_connections(user_id)
        if not group_ids:
            await query.message.edit_text("Connections onnum illa.")
            raise StopPropagation
        buttons = []
        for gid in group_ids:
            try:
                chat = await client.get_chat(int(gid))
                label = chat.title + (" - ACTIVE" if await if_active(user_id, gid) else "")
                buttons.append([InlineKeyboardButton(label, callback_data=f"pgf_grp:{gid}")])
            except Exception:
                continue
        await query.message.edit_text(
            "Connected groups:",
            reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)
        await query.answer()
        raise StopPropagation
