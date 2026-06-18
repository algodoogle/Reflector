# Reflector

A Discord bot that mirrors one server (Server A) to another (Server B) as a live backup. Messages, channels, categories, roles, permissions, and member role assignments are all kept in sync automatically.

---

## Features

### Live mirroring
- Mirrors every message from all text channels in Server A to Server B
- Impersonates the original sender (nickname + avatar) via webhooks
- Copies attachments (images, files, etc.)
- Preserves reply context as a quote prefix
- Mirrors all reactions (emoji) from the original message to the copy, including reactions added after sync
- Auto-creates missing channels in Server B on the fly

### Startup sync
- Syncs all roles (name, colour, permissions) from Server A to Server B
- Syncs all categories and channels (text, voice, stage, forum) including settings (topic, slowmode, NSFW, bitrate, user limit)
- Copies permission overwrites on categories and channels, including `@everyone`
- Copies the guild icon
- Replays message history for each text channel from the last mirrored message onwards (resumable across restarts)
- Assigns Server B members the roles that match their Server A roles

### State files (auto-created at runtime)

All state files are JSON format. They persist across bot restarts so syncing can resume seamlessly.

| File | Purpose | Format |
|---|---|---|
| `mirror_state.json` | Last synced message ID per channel — history sync resumes from here on restart | channel_id : message_id |
| `message_map.json` | Server A message ID → Server B message ID mapping — enables post-sync reaction mirroring | a_message_id : b_message_id |
| `role_map.json` | Server A role ID → Server B role ID mapping — created during startup role sync | a_role_id : b_role_id |
| `category_map.json` | Server A category ID → Server B category ID mapping — used for channel organization | a_category_id : b_category_id |
| `channel_map.json` | Server A text/voice/forum channel ID → Server B channel ID mapping | a_channel_id : b_channel_id |
| `member_roles.json` | Per-user: Server A user ID → list of their Server A role IDs — allows role restoration when Server A is offline | user_id : [role_id, role_id, ...] |
| `sticker_map.json` | Server A sticker ID → Server B sticker ID mapping — synced when stickers are added | a_sticker_id : b_sticker_id |
| `sound_map.json` | Server A soundboard sound ID → Server B sound ID mapping | a_sound_id : b_sound_id |
| `emoji_map.json` | Server A emoji ID → Server B emoji ID mapping — custom emojis are copied when added | a_emoji_id : b_emoji_id |

### Live event handling
| Event | Action |
|---|---|
| New message in Server A | Mirrored to matching channel in Server B with all reactions copied (channel created if missing) |
| Reaction added to message in Server A | Same reaction added to mirrored message in Server B |
| Reaction removed from message in Server A | Same reaction removed from mirrored message in Server B |
| Channel renamed in Server A | Mirror channel renamed to match |
| Channel settings changed | Mirror updated (topic, slowmode, NSFW, permissions) |
| Channel deleted in Server A | Mirror moved to private `Archive` category in Server B |
| New channel created in Server A | Mirror created in Server B |
| Category renamed / permissions changed | Mirror category updated |
| Role created / updated / deleted | Mirror role created / updated / deleted in Server B |
| Member's roles change in Server A | Roles updated in Server B and saved to state |
| Member joins Server B | Their Server A roles restored from saved state (no Server A lookup needed) |
| Guild icon changed in Server A | Icon applied to Server B |
| Emoji added / updated / deleted in Server A | Mirrored to Server B (name, image, role restrictions) |
| Sticker added in Server A | Downloaded and created in Server B |
| Sticker deleted in Server A | Deleted from Server B |

### Roadmap
- [x] Copy between text channels
- [x] Impersonate user with nickname and profile pic
- [x] Copy attachments
- [x] Copy between guilds, creating channels where they don't exist
- [x] Copy message history (resumable)
- [x] Rate limit management
- [x] Archive channel deletions to a private Archive category
- [x] Copy guild icon
- [x] Copy roles
- [x] Copy channel settings and permissions
- [x] Copy channel structure with categories
- [x] Copy all channel types (text, voice, stage, forum)
- [x] Assign roles to users in mirror guild (with saved state)
- [x] Copy soundboard audio files and configuration
- [x] Copy stickers
- [x] Copy emojis

---

## Setup

### 1. Clone and install

```bash
git clone git@github.com:algodoogle/Reflector.git
cd Reflector
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -U pip
pip install discord.py python-dotenv
```

### 2. Create a `.env` file

```
DISCORD_TOKEN=your_bot_token_here
LOG_LEVEL=DEBUG
CHANNEL_BLACKLIST=
```

**Environment variables:**
- `DISCORD_TOKEN` — Your bot's token from the Discord Developer Portal
- `LOG_LEVEL` — Logging level (DEBUG, INFO, WARNING, ERROR)
- `CHANNEL_BLACKLIST` — *Optional* — Comma-separated channel IDs to exclude from startup history sync. Blacklisted channels will still receive live messages after startup completes. Spaces are supported: `123, 456, 789`

### 3. Configure server IDs

Edit the two constants at the top of `bot.py`:

```python
SERVER_A_ID = ...  # Mirror FROM
SERVER_B_ID = ...  # Mirror TO
```

### 4. Bot permissions

The bot needs the following in **both** servers:

- `Administrator` (simplest), or at minimum:
  - Manage Channels
  - Manage Roles
  - Manage Webhooks
  - Read Message History
  - Send Messages
  - View Channels

In the [Discord Developer Portal](https://discord.com/developers/applications), enable these **Privileged Gateway Intents** for your bot:

- **Message Content Intent**
- **Server Members Intent**

> The bot's role must be ranked **above all roles it needs to assign** in Server B's role hierarchy, otherwise role assignment will be silently skipped.

### 5. Run

```bash
python bot.py
```

On first run, Reflector will sync roles, categories, channels, permissions, member roles, and full message history before going live. Subsequent restarts pick up from where they left off.
