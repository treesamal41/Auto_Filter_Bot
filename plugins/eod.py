"""
EOD helper plugin for Carla (admin-only).

Ninte CBS screenshots-ile cash move types implement cheythirikkunnu.
Commands (admins only, private chat recommended):

    /eod IN REMOVED FROM ATM-IN-ATM 500000
    /eod OUT ISSUED FOR REPLENISH-OUT-ATM 1200000
    /eod IN ALB WITHDRAWAL FOR ATM BANK-IN-ATM 2000000
    /eodtally   - IN/OUT totals
    /eodlist    - ellam entries
    /eoddel 2   - #2 delete
    /eodreset   - clear
    /eoddone    - copy-ready EOD summary
    /eodtypes   - cash move type list

Photo book tally (SBI 8-book workflow):
    8 book photos ayakku (caption-il book name koduthal order thettiyal polum
    correct aayi match akum), reply illa,
    /eodbookdone - full CBS line-wise tally oru reply (+ Excel file)
    /eodbookreset - photos clear
    /eodbookorder - book order + kitti tick mark

NOTE: bot CBS site-il touch cheyyilla - nee thanne login cheythu
enter cheyyanam. Entries memory-il aanu (dyno restart aayal pokum).
Photo reading needs GEMINI_API_KEY (Heroku config var).
"""

import base64
import json
import logging
import os
import re
import urllib.request
from datetime import date

from pyrogram import Client, filters, enums
from info import ADMINS

logger = logging.getLogger(__name__)

KNOWN_TYPES = {
    # IN types (from CBS screenshots)
    "REMOVED FROM ATM-IN-ATM",
    "RETURN UNDEPOSITED ATM AMT-IN-ATM",
    "RETURN UNDEPOSITED CIT AMT-IN-CIT",
    "RETURN UNDEPOSITED EP AMT-IN-EP",
    "RETURN UNDEPOSITED OTH AMT-IN-OTH",
    "UNREPLENISHED FROM ROUTE-IN-ATM",
    "VAULT TO VAULT-IN-ATM",
    "VAULT TO VAULT-IN-CIT",
    "VAULT TO VAULT-IN-OTH",
    "WITHDRAWAL FOR ATM BANK-IN-ATM",
    "WITHDRAWAL FOR SORT BANK-IN-ATM",
    "RECEIVED AFTER EXCHANGE-IN-OTH",
    "RECEIVED FROM ENVELOP-IN-EP",
    # OUT types
    "CASH LOSS-OUT-CIT",
    "DEPOSITED TO BANK-OUT-ATM",
    "DEPOSITED TO BANK-OUT-CIT",
    "DEPOSITED TO BANK-OUT-EP",
    "ISSUED FOR REPLENISH-OUT-ATM",
    "ISSUED FOR EXCHANGE-OUT-OTH",
    "VAULT TO VAULT-OUT-ATM",
}

DIRECTIONS = ("IN", "OUT", "OPENING")

_store = {}  # {user_id: [entry, ...]}


def _inr(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        out = s[-3:]
        s = s[:-3]
        while len(s) > 2:
            out = s[-2:] + "," + out
            s = s[:-2]
        out = s + "," + out
    return ("-" if n < 0 else "") + out


def _parse(text: str):
    parts = text.strip().split()
    if len(parts) < 3:
        return "Format: /eod IN REMOVED FROM ATM-IN-ATM 500000"
    direction = parts[0].upper()
    if direction not in DIRECTIONS:
        return "Direction IN / OUT / OPENING aayirikkanam."
    amount_s = parts[-1].replace(",", "")
    if not re.fullmatch(r"\d+", amount_s):
        return "Last-il amount (number) kodukkanam."
    amount = int(amount_s)
    middle = " ".join(parts[1:-1]).upper()
    if not middle:
        return "Cash move type missing."
    bank, ctype, warn, best = "", middle, "", ""
    for known in KNOWN_TYPES:
        if middle == known or middle.endswith(" " + known):
            if len(known) > len(best):
                best = known
    if best:
        prefix = middle[: len(middle) - len(best)].strip()
        if prefix:
            bank = prefix
        ctype = best
    else:
        warn = " (⚠️ list-il illa — spelling check cheyyu)"
    if ("-IN-" in ctype and direction == "OUT") or \
       ("-OUT-" in ctype and direction == "IN"):
        warn += " (⚠️ direction-um type-um mismatch!)"
    return {"direction": direction, "bank": bank, "ctype": ctype,
            "amount": amount, "warn": warn}


def _entries(user_id: int):
    return _store.setdefault(user_id, [])


_eod_only = filters.user(ADMINS) & filters.private


@Client.on_message(filters.command("eod") & _eod_only)
async def eod_add(client, message):
    if len(message.command) < 2:
        await message.reply_text(
            "Usage: <code>/eod IN REMOVED FROM ATM-IN-ATM 500000</code>"
        )
        return
    res = _parse(" ".join(message.command[1:]))
    if isinstance(res, str):
        await message.reply_text(f"❌ {res}")
        return
    es = _entries(message.from_user.id)
    es.append(res)
    bank = f" [{res['bank']}]" if res["bank"] else ""
    await message.reply_text(
        f"✅ #{len(es)} {res['direction']}{bank} {res['ctype']} — "
        f"{_inr(res['amount'])}{res['warn']}"
    )


@Client.on_message(filters.command("eodtally") & _eod_only)
async def eod_tally(client, message):
    es = _entries(message.from_user.id)
    if not es:
        await message.reply_text("No entries yet.")
        return
    totals = {}
    for e in es:
        totals[e["direction"]] = totals.get(e["direction"], 0) + e["amount"]
    lines = [f"{d}: {_inr(totals[d])}" for d in DIRECTIONS if d in totals]
    await message.reply_text("📊 Tally\n" + "\n".join(lines))


@Client.on_message(filters.command("eodlist") & _eod_only)
async def eod_list(client, message):
    es = _entries(message.from_user.id)
    if not es:
        await message.reply_text("No entries yet.")
        return
    lines = []
    for i, e in enumerate(es, 1):
        bank = f" [{e['bank']}]" if e["bank"] else ""
        lines.append(f"{i}. {e['direction']}{bank} {e['ctype']} — {_inr(e['amount'])}")
    await message.reply_text("\n".join(lines))


@Client.on_message(filters.command("eoddel") & _eod_only)
async def eod_del(client, message):
    es = _entries(message.from_user.id)
    try:
        n = int(message.command[1])
        removed = es.pop(n - 1)
        await message.reply_text(f"🗑️ Deleted #{n} ({removed['ctype']})")
    except (IndexError, ValueError):
        await message.reply_text("Usage: <code>/eoddel 2</code>")


@Client.on_message(filters.command("eodreset") & _eod_only)
async def eod_reset(client, message):
    _store[message.from_user.id] = []
    await message.reply_text("🔄 Cleared.")


@Client.on_message(filters.command("eoddone") & _eod_only)
async def eod_done(client, message):
    es = _entries(message.from_user.id)
    if not es:
        await message.reply_text("No entries yet.")
        return
    today = date.today().strftime("%d-%m-%Y")
    out = [f"EOD {today}", ""]
    for d in DIRECTIONS:
        group = [e for e in es if e["direction"] == d]
        if not group:
            continue
        out.append(f"{d}:")
        total = 0
        for e in group:
            bank = f"[{e['bank']}] " if e["bank"] else ""
            out.append(f"  {bank}{e['ctype']} — {_inr(e['amount'])}")
            total += e["amount"]
        out.append(f"  Total {d}: {_inr(total)}")
        out.append("")
    await message.reply_text("\n".join(out))


@Client.on_message(filters.command("eodtypes") & _eod_only)
async def eod_types(client, message):
    ins = sorted(t for t in KNOWN_TYPES if "-IN-" in t)
    outs = sorted(t for t in KNOWN_TYPES if "-OUT-" in t)
    msg = "IN:\n" + "\n".join("• " + t for t in ins)
    msg += "\n\nOUT:\n" + "\n".join("• " + t for t in outs)
    await message.reply_text(msg)


# ---------------------------------------------------------------------------
# Photo book tally — SBI 8-book daily workflow (admin, private chat only)
# 8 photos fixed order-il; reply illa; /eodbookdone -> oru tally reply.
# ---------------------------------------------------------------------------

BOOK_ORDER = [
    ("SBI Aluva Hitachi", "HITACHI"),
    ("SBI Aluva HCMS", "HCMS"),
    ("SBI Treasury Hitachi", "HITACHI"),
    ("SBI Treasury HCMS", "HCMS"),
    ("SBI Mulamthuruthy Hitachi", "HITACHI"),
    ("SBI Mulamthuruthy HCMS", "HCMS"),
    ("SBI Vazhakulam Hitachi", "HITACHI"),
    ("SBI Vazhakulam HCMS", "HCMS"),
]
_BOOK_DENOS = (500, 200, 100)
_book_sessions = {}  # {user_id: {book_name: book_data}}

_GEMINI_PROMPT = """You are reading a cashier's vault register page (a bank cash book).
The page has two sides:
- RIGHT side: OUTWARD PARTICULARS (written in RED pen) = cash ISSUED out (OUT).
  Columns: OUT-DENO (denomination 500/200/100), OUT-PIECES (number of notes).
- LEFT side: INWARD PARTICULARS (written in BLUE pen) = cash RETURNED from route (IN).
  Columns: IN-DENO, IN-PIECES.
There may also be rows for WITHDRAWAL (cash taken from bank, IN side) and
DEPOSIT (cash deposited to bank, OUT side).

Task: sum up ONLY the rows for today's date (ignore opening/closing balance rows).
- "out": total OUT-PIECES per denomination from the red-ink issue rows.
- "in": total IN-PIECES per denomination from the blue-ink return-from-route rows.
- "withdrawal": pieces per denomination from withdrawal rows (0 if none).
- "deposit": pieces per denomination from deposit rows (0 if none).

Reply with ONLY this JSON, no other text:
{"out": {"500": 0, "200": 0, "100": 0}, "in": {"500": 0, "200": 0, "100": 0},
 "withdrawal": {"500": 0, "200": 0, "100": 0}, "deposit": {"500": 0, "200": 0, "100": 0},
 "notes": "any ambiguity, max 1 line"}"""


def _gemini_extract(image_bytes: bytes) -> dict:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    payload = {"contents": [{"parts": [
        {"text": _GEMINI_PROMPT},
        {"inline_data": {"mime_type": "image/jpeg",
                         "data": base64.b64encode(image_bytes).decode()}},
    ]}]}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("Gemini returned no JSON")
    raw = json.loads(m.group(0))

    def clean(d):
        return {str(k): int(v or 0) for k, v in (d or {}).items()
                if str(k) in ("500", "200", "100")}
    return {"out": clean(raw.get("out")), "in": clean(raw.get("in")),
            "withdrawal": clean(raw.get("withdrawal")),
            "deposit": clean(raw.get("deposit")),
            "notes": str(raw.get("notes", ""))[:200]}


def _match_book(caption):
    """Photo caption-il ninnu book kandupidikkuka (order thettiyal polum)."""
    if not caption:
        return None
    t = caption.lower()
    place = None
    for key, name in (("aluva", "Aluva"), ("treasury", "Treasury"),
                      ("mulamt", "Mulamthuruthy"), ("vazhakulam", "Vazhakulam")):
        if key in t:
            place = name
            break
    if not place:
        return None
    if "hcms" in t:
        grp = "HCMS"
    elif "hitachi" in t:
        grp = "HITACHI"
    else:
        return None
    for name, group in BOOK_ORDER:
        if place in name and group == grp:
            return (name, group)
    return None


def _book_combine(books, group, move):
    total = {d: 0 for d in _BOOK_DENOS}
    for b in books:
        if b["group"] != group:
            continue
        for d in _BOOK_DENOS:
            total[d] += int(b.get(move, {}).get(str(d), 0))
    return total


def _book_block(title, pieces):
    lines = [title]
    grand = 0
    for d in _BOOK_DENOS:
        p = pieces[d]
        if p:
            v = p * d
            grand += v
            lines.append(f"{d} x {_inr(p)} = {_inr(v)}")
    lines.append(f"Total = {_inr(grand)}")
    return "\n".join(lines)


def _book_tally(books):
    parts = []
    for group, label in (("HITACHI", "ATM-SBI-HITACHI"), ("HCMS", "ATM-SBI-SBI")):
        parts.append(f"*{label}*")
        for move, title in (
            ("out", "OUT (ISSUED FOR REPLENISH-OUT-ATM)"),
            ("in", "IN (REMOVED FROM ATM-IN-ATM)"),
            ("withdrawal", "WITHDRAWAL (WITHDRAWAL FOR ATM BANK-IN-ATM)"),
            ("deposit", "DEPOSIT (DEPOSITED TO BANK-OUT-ATM)"),
        ):
            c = _book_combine(books, group, move)
            if any(c.values()):
                parts.append(_book_block(title, c))
        parts.append("")
    return "\n".join(parts).strip()


_BOOK_MOVES = (
    ("out", "OUT - Issue"),
    ("in", "IN - Return"),
    ("withdrawal", "Withdrawal"),
    ("deposit", "Deposit"),
)


def _book_excel(books):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "SBI EOD"
    bold = Font(bold=True)
    ws.append([f"SBI EOD Tally - {date.today().strftime('%d-%m-%Y')}"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])

    header = ["Book"]
    for mlabel in ("Issue", "Return", "Withdrawal", "Deposit"):
        for d in _BOOK_DENOS:
            header.append(f"{mlabel} {d}")

    for group, label in (("HITACHI", "HITACHI"), ("HCMS", "HCMS (ATM-SBI-SBI)")):
        ws.append([label])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
        ws.append(header)
        for c in ws[ws.max_row]:
            c.font = bold
        totals = [0] * 12
        for b in books:
            if b["group"] != group:
                continue
            row = [b["name"]]
            i = 0
            for mkey, _ in _BOOK_MOVES:
                for d in _BOOK_DENOS:
                    p = int(b.get(mkey, {}).get(str(d), 0))
                    row.append(p)
                    totals[i] += p
                    i += 1
            ws.append(row)
        ws.append(["TOTAL"] + totals)
        for c in ws[ws.max_row]:
            c.font = bold
        ws.append([])

    ws.column_dimensions["A"].width = 24
    for col in ws.columns:
        letter = col[0].column_letter
        if letter != "A":
            ws.column_dimensions[letter].width = 13
    return wb


@Client.on_message(filters.photo & _eod_only)
async def eod_book_photo(client, message):
    uid = message.from_user.id
    session = _book_sessions.setdefault(uid, {})
    matched = _match_book(message.caption)
    if matched:
        name, group = matched
    else:
        # caption illenkil: fixed order-il varatha first book
        name, group = next(
            ((n, g) for n, g in BOOK_ORDER if n not in session),
            BOOK_ORDER[len(session) % len(BOOK_ORDER)],
        )
    try:
        path = await message.download()
        with open(path, "rb") as f:
            data = _gemini_extract(f.read())
        try:
            os.remove(path)
        except OSError:
            pass
    except RuntimeError as e:
        logger.exception("eod book photo extract failed")
        if "GEMINI_API_KEY" in str(e):
            await message.reply_text(
                f"{name}: Heroku-il GEMINI_API_KEY set cheythittilla. "
                "Config Vars-il add cheythu redeploy cheyyu."
            )
        else:
            await message.reply_text(
                f"{name}: vayikkan pattilla. Photo veendum ayakku."
            )
        return
    except Exception:
        logger.exception("eod book photo extract failed")
        await message.reply_text(
            f"{name}: vayikkan pattilla. Photo veendum ayakku."
        )
        return
    session[name] = {"name": name, "group": group, **data}
    # silent by design — no reply per photo


@Client.on_message(filters.command("eodbookdone") & _eod_only)
async def eod_book_done(client, message):
    uid = message.from_user.id
    session = _book_sessions.get(uid, {})
    books = [session[n] for n, _ in BOOK_ORDER if n in session]
    if not books:
        await message.reply_text("Books onnum illa. Photos ayakku, pinne /eodbookdone.")
        return
    tally = _book_tally(books)
    await message.reply_text(f"SBI EOD Tally ({len(books)} books)\n\n{tally}")
    xlsx_path = f"/tmp/eod_{uid}_{date.today().strftime('%Y%m%d')}.xlsx"
    try:
        _book_excel(books).save(xlsx_path)
        await message.reply_document(xlsx_path, caption="SBI EOD Tally — Excel")
    except Exception:
        logger.exception("eod excel build failed")
    try:
        os.remove(xlsx_path)
    except OSError:
        pass
    _book_sessions.pop(uid, None)


@Client.on_message(filters.command("eodbookreset") & _eod_only)
async def eod_book_reset(client, message):
    _book_sessions.pop(message.from_user.id, None)
    await message.reply_text("Book photos cleared.")


@Client.on_message(filters.command("eodbookorder") & _eod_only)
async def eod_book_order(client, message):
    session = _book_sessions.get(message.from_user.id, {})
    lines = []
    for i, (name, _) in enumerate(BOOK_ORDER):
        mark = "✅" if name in session else f"{i + 1}."
        lines.append(f"{mark} {name}")
    await message.reply_text("Book order:\n" + "\n".join(lines))
