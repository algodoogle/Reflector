import io
import os
import json
import time
import base64
import asyncio
import logging
import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
_fmt   = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_console = logging.StreamHandler()
_console.setFormatter(_fmt)

_file = logging.FileHandler("reflector.log", encoding="utf-8")
_file.setFormatter(_fmt)

logging.root.setLevel(_level)
logging.root.addHandler(_console)
logging.root.addHandler(_file)

log = logging.getLogger("reflector")
TOKEN = os.getenv("DISCORD_TOKEN")

SERVER_A_ID = int(os.getenv("SERVER_A_ID"))  # Mirror FROM
SERVER_B_ID = int(os.getenv("SERVER_B_ID"))  # Mirror TO

ROLE_MAP_FILE        = "role_map.json"
CATEGORY_MAP_FILE    = "category_map.json"
CHANNEL_MAP_FILE     = "channel_map.json"
MESSAGE_STATE_FILE   = "mirror_state.json"
MEMBER_ROLES_FILE    = "member_roles.json"
STICKER_MAP_FILE     = "sticker_map.json"
SOUND_MAP_FILE       = "sound_map.json"
EMOJI_MAP_FILE       = "emoji_map.json"

TIMESTAMP_SUPPRESS_SECONDS = 300  # Omit timestamp if last message from same user within this time 300s = 5 min

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

webhook_cache:  dict[int, discord.Webhook] = {}
role_map:         dict[str, int]        = {}  # str(A role ID)     -> B role ID
category_map:     dict[str, int]        = {}  # str(A category ID) -> B category ID
channel_map:      dict[str, int]        = {}  # str(A channel ID)  -> B channel ID
message_state:    dict[str, int]        = {}  # str(A channel ID)  -> last mirrored message ID
member_roles:     dict[str, list[int]]  = {}  # str(user ID)       -> [A role IDs]
sticker_map:      dict[str, int]        = {}  # str(A sticker ID)  -> B sticker ID
sound_map:        dict[str, int]        = {}  # str(A sound ID)    -> B sound ID
emoji_map:        dict[str, int]        = {}  # str(A emoji ID)    -> B emoji ID
history_sync_complete = False
last_mirrored_message: dict = {}  # Track last message per channel: {channel_id: {"author_id": int, "created_at": datetime}}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_json(path: str) -> dict:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_json(path: str, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_role_map()     -> None:
    global role_map;     role_map     = _load_json(ROLE_MAP_FILE)
def save_role_map()     -> None: _save_json(ROLE_MAP_FILE,     role_map)

def load_category_map() -> None:
    global category_map; category_map = _load_json(CATEGORY_MAP_FILE)
def save_category_map() -> None: _save_json(CATEGORY_MAP_FILE, category_map)

def load_channel_map()  -> None:
    global channel_map;  channel_map  = _load_json(CHANNEL_MAP_FILE)
def save_channel_map()  -> None: _save_json(CHANNEL_MAP_FILE,  channel_map)

def load_message_state() -> None:
    global message_state; message_state = _load_json(MESSAGE_STATE_FILE)
def save_message_state() -> None: _save_json(MESSAGE_STATE_FILE, message_state)

def load_member_roles() -> None:
    global member_roles; member_roles = _load_json(MEMBER_ROLES_FILE)
def save_member_roles() -> None: _save_json(MEMBER_ROLES_FILE, member_roles)

def load_sticker_map() -> None:
    global sticker_map; sticker_map = _load_json(STICKER_MAP_FILE)
def save_sticker_map() -> None: _save_json(STICKER_MAP_FILE, sticker_map)

def load_sound_map() -> None:
    global sound_map; sound_map = _load_json(SOUND_MAP_FILE)
def save_sound_map() -> None: _save_json(SOUND_MAP_FILE, sound_map)

def load_emoji_map() -> None:
    global emoji_map; emoji_map = _load_json(EMOJI_MAP_FILE)
def save_emoji_map() -> None: _save_json(EMOJI_MAP_FILE, emoji_map)


def record_mirrored(channel_id: int, message_id: int) -> None:
    key = str(channel_id)
    if message_state.get(key, 0) < message_id:
        message_state[key] = message_id
        save_message_state()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def format_timestamp_header(message: discord.Message) -> str:
    """Generate Discord timestamp header for a message.
    
    Format: [icon] USERNAME
    -# <t:unix:d> <t:unix:t> (<t:unix:R>)
    
    Where:
    - d = short date (Jan 1, 2021)
    - t = short time (1:23 PM)
    - R = relative time (2 hours ago)
    """
    unix_timestamp = int(message.created_at.timestamp())
    header = (f"-#  {message.author.mention} <t:{unix_timestamp}:d> <t:{unix_timestamp}:t> (<t:{unix_timestamp}:R>)")
    return header


def should_show_timestamp(message: discord.Message, destination: discord.TextChannel) -> bool:
    """Check if timestamp should be shown.
    
    Omit timestamp if the last message in destination was from the same user
    and posted within TIMESTAMP_SUPPRESS_SECONDS.
    """
    last = last_mirrored_message.get(destination.id)
    if last and (last["author_id"] == message.author.id and 
                 (message.created_at - last["created_at"]).total_seconds() < TIMESTAMP_SUPPRESS_SECONDS):
        return False
    return True

async def get_webhook(channel: discord.TextChannel) -> discord.Webhook:
    if channel.id in webhook_cache:
        return webhook_cache[channel.id]
    log.debug("Fetching webhooks for #%s", channel.name)
    webhooks = await channel.webhooks()
    webhook = discord.utils.get(webhooks, name="MirrorWebhook")
    if webhook is None:
        log.debug("Creating MirrorWebhook in #%s", channel.name)
        webhook = await channel.create_webhook(name="MirrorWebhook")
    webhook_cache[channel.id] = webhook
    return webhook


def translate_overwrites(source_overwrites: dict, guild_b: discord.Guild) -> dict:
    """Translate Server A role permission overwrites to Server B equivalents."""
    result = {}
    for target, overwrite in source_overwrites.items():
        if isinstance(target, discord.Role):
            if target.is_default():
                # @everyone maps directly to @everyone in Server B
                result[guild_b.default_role] = overwrite
            else:
                mapped_id = role_map.get(str(target.id))
                if mapped_id:
                    b_role = guild_b.get_role(mapped_id)
                    if b_role:
                        result[b_role] = overwrite
    return result


async def get_or_create_mirror_category(
    source_cat: discord.CategoryChannel, guild_b: discord.Guild, *, _sync: bool = False
) -> discord.CategoryChannel:
    key = str(source_cat.id)

    # If already mapped, optionally sync name+permissions and return
    if key in category_map:
        cat = guild_b.get_channel(category_map[key])
        if cat:
            if _sync:
                overwrites = translate_overwrites(source_cat.overwrites, guild_b)
                log.debug("Syncing category '%s'", source_cat.name)
                await cat.edit(name=source_cat.name, overwrites=overwrites)
            return cat

    # Find by name or create fresh
    overwrites = translate_overwrites(source_cat.overwrites, guild_b)
    cat = discord.utils.get(guild_b.categories, name=source_cat.name)
    if cat is None:
        log.debug("Creating category '%s'", source_cat.name)
        cat = await guild_b.create_category(name=source_cat.name, overwrites=overwrites)
    else:
        log.debug("Adopting existing category '%s'", source_cat.name)
        await cat.edit(overwrites=overwrites)

    category_map[key] = cat.id
    save_category_map()
    return cat


async def get_or_create_mirror_channel(
    source: discord.abc.GuildChannel, guild_b: discord.Guild | None = None
) -> discord.abc.GuildChannel | None:
    """
    Find or create the Server B mirror of any non-category channel.
    Returns None for unsupported channel types.
    """
    if guild_b is None:
        guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        raise RuntimeError("Bot is not a member of Server B")

    # Resolve mirror category first
    mirror_cat = None
    if source.category:
        mirror_cat = await get_or_create_mirror_category(source.category, guild_b)

    overwrites = translate_overwrites(source.overwrites, guild_b)

    # Look up existing mirror via channel map
    key = str(source.id)
    if key in channel_map:
        existing = guild_b.get_channel(channel_map[key])
        if existing:
            # Sync permissions and settings on the existing channel
            await _sync_channel_settings(source, existing, guild_b, overwrites, mirror_cat)
            return existing

    # Create new mirror channel by type
    mirror = None

    if isinstance(source, discord.TextChannel):
        mirror = discord.utils.get(guild_b.text_channels, name=source.name)
        if mirror is None:
            log.debug("Creating text channel #%s", source.name)
            mirror = await guild_b.create_text_channel(
                name=source.name,
                category=mirror_cat,
                topic=source.topic,
                slowmode_delay=source.slowmode_delay,
                nsfw=source.nsfw,
                overwrites=overwrites or {},
            )
        else:
            await _sync_channel_settings(source, mirror, guild_b, overwrites, mirror_cat)

    elif isinstance(source, discord.VoiceChannel):
        mirror = discord.utils.get(guild_b.voice_channels, name=source.name)
        if mirror is None:
            log.debug("Creating voice channel #%s", source.name)
            mirror = await guild_b.create_voice_channel(
                name=source.name,
                category=mirror_cat,
                bitrate=min(source.bitrate, guild_b.bitrate_limit),
                user_limit=source.user_limit,
                overwrites=overwrites or {},
            )
        else:
            await _sync_channel_settings(source, mirror, guild_b, overwrites, mirror_cat)

    elif isinstance(source, discord.StageChannel):
        mirror = discord.utils.get(guild_b.stage_channels, name=source.name)
        if mirror is None:
            log.debug("Creating stage channel #%s", source.name)
            mirror = await guild_b.create_stage_channel(
                name=source.name,
                category=mirror_cat,
                overwrites=overwrites or {},
            )
        else:
            await _sync_channel_settings(source, mirror, guild_b, overwrites, mirror_cat)

    elif isinstance(source, discord.ForumChannel):
        mirror = discord.utils.get(guild_b.forums, name=source.name)
        if mirror is None:
            log.debug("Creating forum channel #%s", source.name)
            mirror = await guild_b.create_forum(
                name=source.name,
                category=mirror_cat,
                topic=source.topic,
                overwrites=overwrites or {},
            )
        else:
            await _sync_channel_settings(source, mirror, guild_b, overwrites, mirror_cat)

    if mirror is None:
        return None

    channel_map[key] = mirror.id
    save_channel_map()
    return mirror


async def _sync_channel_settings(
    source: discord.abc.GuildChannel,
    mirror: discord.abc.GuildChannel,
    guild_b: discord.Guild,
    overwrites: dict,
    mirror_cat: discord.CategoryChannel | None,
) -> None:
    """Push name, category, permissions, and type-specific settings onto an existing mirror."""
    edits: dict = {}

    if mirror.name != source.name:
        edits["name"] = source.name

    source_cat_id = source.category.id if source.category else None
    mirror_cat_id = mirror.category.id if mirror.category else None
    if source_cat_id != mirror_cat_id:
        edits["category"] = mirror_cat

    # Always apply overwrites — an empty dict clears permissions, so we can't skip it
    edits["overwrites"] = overwrites

    if isinstance(source, discord.TextChannel) and isinstance(mirror, discord.TextChannel):
        if mirror.topic != source.topic:
            edits["topic"] = source.topic
        if mirror.slowmode_delay != source.slowmode_delay:
            edits["slowmode_delay"] = source.slowmode_delay
        if mirror.nsfw != source.nsfw:
            edits["nsfw"] = source.nsfw

    elif isinstance(source, discord.VoiceChannel) and isinstance(mirror, discord.VoiceChannel):
        capped = min(source.bitrate, guild_b.bitrate_limit)
        if mirror.bitrate != capped:
            edits["bitrate"] = capped
        if mirror.user_limit != source.user_limit:
            edits["user_limit"] = source.user_limit

    if edits:
        log.debug("Updating channel #%s: %s", mirror.name, list(edits.keys()))
        await mirror.edit(**edits)


async def mirror_message(
    message: discord.Message, destination: discord.TextChannel
) -> bool:
    """Send one message to the destination channel via webhook. Returns True if sent."""
    webhook = await get_webhook(destination)

    # Attachments larger than Server B's upload limit can't be re-uploaded
    # (413 Payload Too Large) — link to the original instead.
    size_limit = destination.guild.filesize_limit
    files = []
    oversized_links = []
    for attachment in message.attachments:
        if attachment.size > size_limit:
            log.warning(
                "Attachment '%s' (%d B) exceeds Server B limit (%d B) — linking instead",
                attachment.filename, attachment.size, size_limit,
            )
            oversized_links.append(attachment.url)
            continue
        files.append(await attachment.to_file())

    content = message.content
    
    # Prepend timestamp header (unless last message was from same user within threshold)
    if should_show_timestamp(message, destination):
        timestamp_header = format_timestamp_header(message)
        content = f"{timestamp_header}\n{content}" if content else timestamp_header

    if message.reference:
        try:
            replied = await message.channel.fetch_message(message.reference.message_id)
            content = (
                f"> Replying to {replied.author.display_name}: "
                f"{replied.content[:100]}\n\n{content}"
            )
        except Exception:
            pass

    if oversized_links:
        content = (content + "\n" + "\n".join(oversized_links)).strip()

    if not content and not files:
        return False

    await webhook.send(
        content=content or None,
        username=message.author.display_name,
        avatar_url=message.author.display_avatar.url,
        files=files,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    
    # Track this message for future timestamp suppression checks
    last_mirrored_message[destination.id] = {
        "author_id": message.author.id,
        "created_at": message.created_at,
    }
    
    return True


def mapped_roles_for_member(
    member: discord.Member, guild_b: discord.Guild
) -> list[discord.Role]:
    result = []
    for role in member.roles:
        if role.is_default():
            continue
        mapped_id = role_map.get(str(role.id))
        if mapped_id:
            b_role = guild_b.get_role(mapped_id)
            if b_role:
                result.append(b_role)
    return result


async def apply_member_roles(member_a: discord.Member, guild_b: discord.Guild) -> None:
    """Sync a Server A member's roles to Server B and persist the role state."""
    role_ids = [r.id for r in member_a.roles if not r.is_default()]
    member_roles[str(member_a.id)] = role_ids
    save_member_roles()

    b_member = guild_b.get_member(member_a.id)
    if b_member:
        await _assign_roles_in_b(b_member)


async def _assign_roles_in_b(b_member: discord.Member) -> None:
    """Assign Server B roles from the saved member_roles state. No Server A lookup needed."""
    guild_b = b_member.guild
    bot_member = guild_b.get_member(bot.user.id)
    bot_top = bot_member.top_role.position if bot_member else 0

    desired = []
    for a_role_id in member_roles.get(str(b_member.id), []):
        mapped_id = role_map.get(str(a_role_id))
        if mapped_id:
            b_role = guild_b.get_role(mapped_id)
            desired.append(b_role)

    desired_ids = {r.id for r in desired}
    current_ids = {r.id for r in b_member.roles if not r.is_default()}
    if desired_ids == current_ids:
        return

    try:
        log.debug("Updating roles for member %s", b_member)
        await b_member.edit(roles=desired, reason="Reflector: sync member roles")
    except discord.Forbidden:
        log.warning("Cannot assign roles to %s — check bot role hierarchy in Server B", b_member)


# ---------------------------------------------------------------------------
# Bulk sync helpers
# ---------------------------------------------------------------------------

async def sync_roles(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    changed = False
    for role in guild_a.roles:
        if role.is_default():
            continue
        key = str(role.id)
        if key in role_map:
            b_role = guild_b.get_role(role_map[key])
            if b_role:
                log.debug("Updating role '%s'", role.name)
                await b_role.edit(
                    name=role.name,
                    color=role.color,
                    permissions=role.permissions,
                    reason="Reflector: sync role",
                )
        else:
            log.debug("Creating role '%s'", role.name)
            b_role = await guild_b.create_role(
                name=role.name,
                color=role.color,
                permissions=role.permissions,
                reason="Reflector: sync role",
            )
            role_map[key] = b_role.id
            changed = True
    if changed:
        save_role_map()


async def sync_channel_positions(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    """Bulk-update all channel and category positions in Server B to match Server A.

    Uses the raw ``PATCH /guilds/{id}/channels`` endpoint (one request for every
    move) rather than editing channels individually, which gets rate-limited hard.

    Note: this only sends ``position`` — Discord rejects a bulk request that
    changes ``parent_id`` on more than one channel (error 40009). Moving a channel
    into a different category is handled by ``_sync_channel_settings`` instead.
    """
    moves: list[dict] = []

    for cat in guild_a.categories:
        mirror_cat = guild_b.get_channel(category_map.get(str(cat.id), 0))
        if mirror_cat:
            moves.append({"id": mirror_cat.id, "position": cat.position})

    for ch in guild_a.channels:
        if isinstance(ch, discord.CategoryChannel):
            continue
        mirror_ch = guild_b.get_channel(channel_map.get(str(ch.id), 0))
        if mirror_ch:
            moves.append({"id": mirror_ch.id, "position": ch.position})

    if not moves:
        return
    try:
        log.debug("Bulk updating positions for %d channels/categories", len(moves))
        await bot.http.bulk_channel_update(guild_b.id, moves)
    except Exception as e:
        log.warning("Could not bulk update channel positions: %s", e)


_position_sync_task: asyncio.Task | None = None


def _schedule_position_sync() -> None:
    global _position_sync_task
    if _position_sync_task and not _position_sync_task.done():
        _position_sync_task.cancel()
    _position_sync_task = asyncio.create_task(_run_position_sync())


async def _run_position_sync() -> None:
    await asyncio.sleep(2)
    guild_a = bot.get_guild(SERVER_A_ID)
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_a and guild_b:
        log.debug("Running debounced position sync")
        await sync_channel_positions(guild_a, guild_b)


async def sync_channel_structure(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    """Create/update all categories and channels from Server A in Server B."""
    # Sync category names + permissions first (once each, not once per channel)
    for cat in guild_a.categories:
        await get_or_create_mirror_category(cat, guild_b, _sync=True)

    # All other channels (text, voice, stage, forum)
    for channel in guild_a.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue
        try:
            await get_or_create_mirror_channel(channel, guild_b)
        except Exception as e:
            log.warning("Could not mirror channel #%s: %s", channel.name, e)

    # Fix all positions in one bulk call
    await sync_channel_positions(guild_a, guild_b)


async def sync_member_roles(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    for member in guild_a.members:
        await apply_member_roles(member, guild_b)
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Sticker and soundboard sync
# ---------------------------------------------------------------------------

async def _download(url: str) -> bytes:
    log.debug("GET %s", url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


async def _api(route: discord.http.Route, **kwargs):
    log.debug("API %s %s", route.method, route.path)
    return await bot.http.request(route, **kwargs)


async def sync_stickers(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    try:
        stickers_a = await guild_a.fetch_stickers()
    except discord.HTTPException:
        log.warning("Could not fetch stickers from Server A")
        return

    log.debug("Fetching stickers from Server B")
    b_stickers = await guild_b.fetch_stickers()
    b_names = {s.name for s in b_stickers}

    for sticker in stickers_a:
        key = str(sticker.id)
        if key in sticker_map:
            continue
        if sticker.name in b_names:
            existing = discord.utils.get(b_stickers, name=sticker.name)
            if existing:
                sticker_map[key] = existing.id
                save_sticker_map()
            continue

        try:
            data = await _download(sticker.url)
            ext = "json" if sticker.format is discord.StickerFormatType.lottie else sticker.format.name
            file = discord.File(io.BytesIO(data), filename=f"{sticker.name}.{ext}")
            log.debug("Creating sticker '%s'", sticker.name)
            b_sticker = await guild_b.create_sticker(
                name=sticker.name,
                description=sticker.description or sticker.name,
                emoji=sticker.emoji,
                file=file,
                reason="Reflector: sync sticker",
            )
            sticker_map[key] = b_sticker.id
            save_sticker_map()
            b_names.add(sticker.name)
            await asyncio.sleep(1)  # sticker creation is heavily rate-limited
        except Exception as e:
            log.warning("Could not mirror sticker '%s': %s", sticker.name, e)


async def sync_soundboard(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    try:
        route = discord.http.Route("GET", "/guilds/{guild_id}/soundboard-sounds", guild_id=guild_a.id)
        payload = await _api(route)
        sounds_a = payload.get("items", []) if isinstance(payload, dict) else payload
    except Exception as e:
        log.warning("Could not fetch soundboard from Server A: %s", e)
        return

    try:
        route = discord.http.Route("GET", "/guilds/{guild_id}/soundboard-sounds", guild_id=guild_b.id)
        payload = await _api(route)
        sounds_b = payload.get("items", []) if isinstance(payload, dict) else payload
        b_sound_names = {s["name"] for s in sounds_b}
    except Exception:
        sounds_b = []
        b_sound_names = set()

    for sound in sounds_a:
        sound_id = sound["sound_id"]
        key = str(sound_id)
        if key in sound_map:
            continue
        if sound["name"] in b_sound_names:
            existing = next((s for s in sounds_b if s["name"] == sound["name"]), None)
            if existing:
                sound_map[key] = int(existing["sound_id"])
                save_sound_map()
            continue

        try:
            cdn_url = f"https://cdn.discordapp.com/soundboard-sounds/{sound_id}"
            audio = await _download(cdn_url)
            b64 = base64.b64encode(audio).decode()
            body = {
                "name": sound["name"],
                "sound": f"data:audio/ogg;base64,{b64}",
                "volume": sound.get("volume", 1.0),
            }
            if sound.get("emoji_name"):
                body["emoji_name"] = sound["emoji_name"]
            if sound.get("emoji_id"):
                body["emoji_id"] = sound["emoji_id"]

            route = discord.http.Route("POST", "/guilds/{guild_id}/soundboard-sounds", guild_id=guild_b.id)
            result = await _api(route, json=body)
            sound_map[key] = int(result["sound_id"])
            save_sound_map()
            b_sound_names.add(sound["name"])
            await asyncio.sleep(1)
        except Exception as e:
            log.warning("Could not mirror sound '%s': %s", sound['name'], e)

    # Remove sounds deleted from Server A
    a_sound_ids = {str(s["sound_id"]) for s in sounds_a}
    for a_id in list(sound_map.keys()):
        if a_id not in a_sound_ids:
            b_id = sound_map.pop(a_id)
            try:
                route = discord.http.Route(
                    "DELETE",
                    "/guilds/{guild_id}/soundboard-sounds/{sound_id}",
                    guild_id=guild_b.id,
                    sound_id=b_id,
                )
                await _api(route)
            except Exception as e:
                log.warning("Could not delete orphaned mirror sound %s: %s", b_id, e)
    save_sound_map()


@tasks.loop(hours=1)
async def hourly_soundboard_sync() -> None:
    guild_a = bot.get_guild(SERVER_A_ID)
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_a and guild_b:
        await sync_soundboard(guild_a, guild_b)


@hourly_soundboard_sync.before_loop
async def before_hourly_soundboard_sync() -> None:
    await bot.wait_until_ready()


async def sync_emojis(guild_a: discord.Guild, guild_b: discord.Guild) -> None:
    b_emojis = guild_b.emojis
    b_names  = {e.name for e in b_emojis}

    for emoji in guild_a.emojis:
        key = str(emoji.id)
        if key in emoji_map:
            continue
        if emoji.name in b_names:
            existing = discord.utils.get(b_emojis, name=emoji.name)
            if existing:
                emoji_map[key] = existing.id
                save_emoji_map()
            continue

        try:
            image = await emoji.read()
            roles = [
                guild_b.get_role(role_map[str(r.id)])
                for r in emoji.roles
                if str(r.id) in role_map and guild_b.get_role(role_map[str(r.id)])
            ]
            log.debug("Creating emoji '%s'", emoji.name)
            b_emoji = await guild_b.create_custom_emoji(
                name=emoji.name,
                image=image,
                roles=roles,
                reason="Reflector: sync emoji",
            )
            emoji_map[key] = b_emoji.id
            save_emoji_map()
            b_names.add(emoji.name)
            await asyncio.sleep(0.5)
        except discord.HTTPException as e:
            log.warning("Could not mirror emoji '%s': %s", emoji.name, e)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s", bot.user)

    # Load all persistent state up front so event handlers work immediately
    load_role_map()
    load_category_map()
    load_channel_map()
    load_message_state()
    load_member_roles()
    load_sticker_map()
    load_sound_map()
    load_emoji_map()

    guild_a = bot.get_guild(SERVER_A_ID)
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_a is None or guild_b is None:
        log.error("Bot is not in both guilds.")
        return

    #log.info("Syncing roles...")
    #await sync_roles(guild_a, guild_b)

    #log.info("Syncing channel structure and permissions...")
    #await sync_channel_structure(guild_a, guild_b)

    log.info("Syncing guild icon...")
    if guild_a.icon:
        log.debug("Updating guild icon from Server A")
        await guild_b.edit(icon=await guild_a.icon.read())

    #log.info("Syncing member roles...")
    #await sync_member_roles(guild_a, guild_b)

    #log.info("Syncing emojis...")
    #await sync_emojis(guild_a, guild_b)

    #log.info("Syncing stickers...")
    #await sync_stickers(guild_a, guild_b)

    """
    log.info("Syncing soundboard...")
    await sync_soundboard(guild_a, guild_b)
    if not hourly_soundboard_sync.is_running():
        hourly_soundboard_sync.start()
    """

    log.info("Syncing message history...")
    for channel in guild_a.text_channels:
        mirror = await get_or_create_mirror_channel(channel, guild_b)
        if not isinstance(mirror, discord.TextChannel):
            continue

        last_id = message_state.get(str(channel.id))
        after = discord.Object(id=last_id) if last_id else None

        async for message in channel.history(after=after, oldest_first=True, limit=None):
            record_mirrored(channel.id, message.id)
            if message.author.bot:
                continue
            try:
                await mirror_message(message, mirror)
            except Exception as e:
                log.warning("Could not mirror message %s in #%s: %s", message.id, channel.name, e)
            await asyncio.sleep(0.5)

    global history_sync_complete
    history_sync_complete = True
    log.info("Startup sync complete — now mirroring live messages.")


# ---------------------------------------------------------------------------
# Live message mirroring
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message) -> None:
    if not history_sync_complete:
        return
    if not message.guild or message.guild.id != SERVER_A_ID:
        return
    if not isinstance(message.channel, discord.TextChannel):
        return

    guild_b = bot.get_guild(SERVER_B_ID)
    destination = await get_or_create_mirror_channel(message.channel, guild_b)
    if not isinstance(destination, discord.TextChannel):
        return

    sent = await mirror_message(message, destination)
    if sent:
        record_mirrored(message.channel.id, message.id)
    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Channel events
# ---------------------------------------------------------------------------

@bot.event
async def on_guild_channel_create(channel) -> None:
    if channel.guild.id != SERVER_A_ID:
        return
    if isinstance(channel, discord.CategoryChannel):
        return

    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b:
        try:
            await get_or_create_mirror_channel(channel, guild_b)
        except Exception as e:
            log.warning("Could not create mirror for #%s: %s", channel.name, e)


@bot.event
async def on_guild_channel_update(before, after) -> None:
    if before.guild.id != SERVER_A_ID:
        return

    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return

    if isinstance(after, discord.CategoryChannel):
        key = str(after.id)
        if key not in category_map:
            return
        mirror_cat = guild_b.get_channel(category_map[key])
        if mirror_cat:
            log.debug("Updating category '%s'", after.name)
            await mirror_cat.edit(
                name=after.name,
                overwrites=translate_overwrites(after.overwrites, guild_b),
            )
        if before.position != after.position:
            _schedule_position_sync()
        return

    key = str(after.id)
    if key not in channel_map:
        return
    mirror = guild_b.get_channel(channel_map[key])
    if mirror:
        mirror_cat = None
        if after.category:
            mirror_cat = await get_or_create_mirror_category(after.category, guild_b)
        await _sync_channel_settings(
            after, mirror, guild_b,
            translate_overwrites(after.overwrites, guild_b),
            mirror_cat,
        )
        if before.position != after.position or before.category != after.category:
            _schedule_position_sync()


@bot.event
async def on_guild_channel_delete(channel) -> None:
    if channel.guild.id != SERVER_A_ID:
        return
    if isinstance(channel, discord.CategoryChannel):
        return

    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return

    key = str(channel.id)
    mirror_id = channel_map.get(key)
    mirror = guild_b.get_channel(mirror_id) if mirror_id else None

    # Fall back to name search for text channels
    if mirror is None and isinstance(channel, discord.TextChannel):
        mirror = discord.utils.get(guild_b.text_channels, name=channel.name)

    if mirror is None:
        return

    if isinstance(mirror, discord.TextChannel):
        archive_cat = discord.utils.get(guild_b.categories, name="Archive")
        if archive_cat is None:
            log.debug("Creating Archive category")
            archive_cat = await guild_b.create_category("Archive")
        log.debug("Archiving channel #%s", mirror.name)
        await mirror.edit(
            category=archive_cat,
            overwrites={guild_b.default_role: discord.PermissionOverwrite(view_channel=False)},
            reason="Reflector: source channel deleted",
        )
    else:
        # For non-text channels, just delete the mirror outright
        log.debug("Deleting mirror channel #%s", mirror.name)
        await mirror.delete(reason="Reflector: source channel deleted")

    channel_map.pop(key, None)
    save_channel_map()


# ---------------------------------------------------------------------------
# Guild events
# ---------------------------------------------------------------------------

@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    if after.id != SERVER_A_ID or before.icon == after.icon:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b and after.icon:
        log.debug("Updating guild icon")
        await guild_b.edit(icon=await after.icon.read())


# ---------------------------------------------------------------------------
# Role events
# ---------------------------------------------------------------------------

@bot.event
async def on_guild_role_create(role: discord.Role) -> None:
    if role.guild.id != SERVER_A_ID:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return
    log.debug("Creating role '%s'", role.name)
    b_role = await guild_b.create_role(
        name=role.name, color=role.color, permissions=role.permissions,
        reason="Reflector: role created in source",
    )
    role_map[str(role.id)] = b_role.id
    save_role_map()


@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    if after.guild.id != SERVER_A_ID:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return
    mapped_id = role_map.get(str(after.id))
    if not mapped_id:
        return
    b_role = guild_b.get_role(mapped_id)
    if b_role:
        log.debug("Updating role '%s'", after.name)
        await b_role.edit(
            name=after.name, color=after.color, permissions=after.permissions,
            reason="Reflector: role updated in source",
        )


@bot.event
async def on_guild_role_delete(role: discord.Role) -> None:
    if role.guild.id != SERVER_A_ID:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return
    mapped_id = role_map.pop(str(role.id), None)
    if mapped_id is None:
        return
    b_role = guild_b.get_role(mapped_id)
    if b_role:
        log.debug("Deleting role id=%s", mapped_id)
        await b_role.delete(reason="Reflector: role deleted in source")
    save_role_map()


# ---------------------------------------------------------------------------
# Member events
# ---------------------------------------------------------------------------

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if after.guild.id != SERVER_A_ID or before.roles == after.roles:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b:
        await apply_member_roles(after, guild_b)


@bot.event
async def on_guild_emojis_update(
    guild: discord.Guild,
    before: list[discord.Emoji],
    after: list[discord.Emoji],
) -> None:
    if guild.id != SERVER_A_ID:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return

    before_ids = {e.id for e in before}
    after_ids  = {e.id for e in after}

    # Created
    if any(e.id not in before_ids for e in after):
        await sync_emojis(guild, guild_b)

    # Deleted
    for emoji in before:
        if emoji.id not in after_ids:
            key = str(emoji.id)
            b_id = emoji_map.pop(key, None)
            if b_id:
                b_emoji = discord.utils.get(guild_b.emojis, id=b_id)
                if b_emoji:
                    try:
                        log.debug("Deleting emoji id=%s", b_id)
                        await b_emoji.delete(reason="Reflector: source emoji deleted")
                    except discord.HTTPException:
                        pass
            save_emoji_map()

    # Updated (name or role restrictions changed)
    for emoji in after:
        if emoji.id in before_ids:
            old = discord.utils.get(before, id=emoji.id)
            if old and (old.name != emoji.name or old.roles != emoji.roles):
                b_id = emoji_map.get(str(emoji.id))
                if b_id:
                    b_emoji = discord.utils.get(guild_b.emojis, id=b_id)
                    if b_emoji:
                        roles = [
                            guild_b.get_role(role_map[str(r.id)])
                            for r in emoji.roles
                            if str(r.id) in role_map and guild_b.get_role(role_map[str(r.id)])
                        ]
                        try:
                            log.debug("Updating emoji '%s'", emoji.name)
                            await b_emoji.edit(
                                name=emoji.name,
                                roles=roles,
                                reason="Reflector: emoji updated",
                            )
                        except discord.HTTPException as e:
                            log.warning("Could not update emoji '%s': %s", emoji.name, e)


@bot.event
async def on_guild_stickers_update(
    guild: discord.Guild,
    before: list[discord.GuildSticker],
    after: list[discord.GuildSticker],
) -> None:
    if guild.id != SERVER_A_ID:
        return
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return

    before_ids = {s.id for s in before}
    after_ids  = {s.id for s in after}

    # New stickers
    for sticker in after:
        if sticker.id not in before_ids:
            await sync_stickers(guild, guild_b)
            break  # sync_stickers handles all missing ones at once

    # Deleted stickers
    for sticker in before:
        if sticker.id not in after_ids:
            key = str(sticker.id)
            b_id = sticker_map.pop(key, None)
            if b_id:
                b_sticker = discord.utils.get(await guild_b.fetch_stickers(), id=b_id)
                if b_sticker:
                    try:
                        log.debug("Deleting sticker id=%s", b_id)
                        await b_sticker.delete(reason="Reflector: source sticker deleted")
                    except discord.HTTPException:
                        pass
                save_sticker_map()


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if member.guild.id != SERVER_B_ID:
        return
    # member object is passed directly — no cache lookup, no Server A needed
    await _assign_roles_in_b(member)


bot.run(TOKEN)
