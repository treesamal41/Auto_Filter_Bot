<h1 align="center">🤖 Carla — Auto Filter Bot</h1>

<p align="center">
  <b>A powerful Telegram auto-filter bot with series season picker, per-group filters, subtitles, voice search & more.</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/Pyrogram-2.x-FF6B6B?style=for-the-badge&logo=telegram&logoColor=white" alt="Pyrogram">
  <img src="https://img.shields.io/badge/MongoDB-Database-47A248?style=for-the-badge&logo=mongodb&logoColor=white" alt="MongoDB">
  <img src="https://img.shields.io/badge/Heroku-Ready-430098?style=for-the-badge&logo=heroku&logoColor=white" alt="Heroku">
</p>

---

## ✨ Features

### 🔍 Smart Auto Filter
- Just type a movie/series name — no commands needed
- IMDb/TMDB poster, rating, cast & synopsis with every result
- **Quality / Language / Season** filter buttons
- **Send All** button, fast pagination, spell-check suggestions

### 📺 Series Auto Season Picker
- Search a series (e.g. `Breaking Bad`) → **season buttons appear automatically**
- Shows the **actual season count** from IMDb/TMDB — no fake fixed list
- Tap a season → episode files appear; **Back to Files** returns to the full list
- Movies are untouched — they keep the normal file list view

### 🔗 Group Invite Links
- `/invitelink` — get the current group's invite link (admins)
- `/grouplinks` — owner-only list of all group links (bot PM)

### 💬 Subtitles
- Subtitle button on every result, powered by OpenSubtitles
- `/subdl` with IMDb ID for direct subtitle download

### 🎙️ Voice Search
- Send a **voice message** with the movie name → transcribed via Whisper → searched automatically

### 🛠️ Admin Tools
- Channel/group file indexing (`/index`)
- Broadcast, ban/unban, stats, top searches
- Force subscribe, 3-step verification, per-group settings (`/settings`)
- Custom captions, IMDb templates, file deletion tools

### 🪶 Lightweight
Premium, Redeem and URL-Shortener modules were removed to keep memory usage low and the bot fast.

---

## ⌨️ Commands

### 👤 User Commands
| Command | Description |
|---|---|
| `/start` | Start the bot / check alive |
| `/help` | How to request movies & series |
| `/about` | Bot details |
| `/imdb <name>` | IMDb details of a movie/series |
| `/subdl <imdb_id>` | Download subtitles |
| `/request <name>` | Request a movie in the request channel |
| `/verify` | Verify yourself (if verification is on) |
| `/settings` | Group settings (admins) |

### 🔗 Invite Commands
| Command | Description |
|---|---|
| `/invitelink` | Group invite link (group admins) |
| `/grouplinks` | All group links (owner, bot PM) |

### 👑 Owner Commands
| Command | Description |
|---|---|
| `/broadcast` | Broadcast a message |
| `/stats` | Bot statistics |
| `/ban` / `/unban` | Ban / unban users |
| `/index` | Index channel files |
| `/deletefiles` | Delete indexed files |
| `/restart` | Restart the bot |
| `/logs` | Get log file |

---

## 🚀 Deploy

### Heroku (easiest)
1. Fork this repo (or import it)
2. Create a new app on [Heroku](https://heroku.com)
3. Connect the app to your GitHub repo → **Deploy Branch**
4. Add the **Config Vars** below → the app restarts automatically

### VPS
1. `git clone` the repo
2. `pip install -r requirements.txt`
3. Fill in `info.py` or set environment variables
4. `python3 bot.py`

---

## ⚙️ Important Config Vars

| Variable | Description |
|---|---|
| `API_ID` / `API_HASH` | From [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `MONGO_URI` | MongoDB connection string |
| `ADMINS` | Bot admin user IDs (space separated) |
| `CHANNELS` | Index channel IDs |
| `OWNER_UPI_ID` | UPI ID shown for donations |
| `QR_CODE` | Donation QR image URL |
| `OPENAI_API_KEY` | Required for voice search |

Full list of variables is documented in [`info.py`](info.py).

---

## 📁 Plugin Files

| File | Feature |
|---|---|
| `plugins/pmfilter.py` | Auto filter + series season picker |
| `plugins/invitelink.py` | Group invite links |
| `plugins/subtitle.py` | Subtitle download |
| `plugins/voice_search.py` | Voice message search |
| `plugins/imdb.py` | `/imdb` details |
| `plugins/commands.py` | Core commands |

> Upload every plugin file above to keep all features working.

---

## 📝 Notes
- The bot needs to be **admin** in groups/channels where it posts links or indexes files.
- For invite links, the bot needs the *invite users* admin right.
- Intended for personal/educational use — respect Telegram's rules and copyright laws.
