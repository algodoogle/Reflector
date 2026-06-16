import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Guild IDs
SERVER_A_ID = 1516375508747685958  # Mirror FROM
SERVER_B_ID = 494916777340436490    # Mirror TO

# Enable required intents (message content is privileged — must be enabled in Dev Portal)
intents = discord.Intents.default()
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# Cache webhooks so we don't recreate/fetch them every message
webhook_cache = {}


async def get_webhook(channel: discord.TextChannel):
    """Get or create a webhook for a destination channel."""
    if channel.id in webhook_cache:
        return webhook_cache[channel.id]

    webhooks = await channel.webhooks()
    webhook = discord.utils.get(webhooks, name="MirrorWebhook")
    if webhook is None:
        webhook = await channel.create_webhook(name="MirrorWebhook")

    webhook_cache[channel.id] = webhook
    return webhook


async def get_or_create_mirror_channel(
    source_channel: discord.TextChannel,
) -> discord.TextChannel:
    """Find or create a channel in Server B matching the source channel's name."""
    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        raise RuntimeError("Bot is not a member of Server B")

    mirror = discord.utils.get(guild_b.text_channels, name=source_channel.name)
    if mirror is None:
        mirror = await guild_b.create_text_channel(name=source_channel.name)

    return mirror


@bot.event
async def on_message(message: discord.Message):
    """Mirror every message from any text channel in Server A to Server B."""
    if message.author.bot:
        return

    if not message.guild or message.guild.id != SERVER_A_ID:
        return

    if not isinstance(message.channel, discord.TextChannel):
        return

    destination = await get_or_create_mirror_channel(message.channel)
    webhook = await get_webhook(destination)

    files = []
    for attachment in message.attachments:
        files.append(await attachment.to_file())

    content = message.content

    if message.reference:
        try:
            replied = await message.channel.fetch_message(message.reference.message_id)
            content = (
                f"> Replying to {replied.author.display_name}: "
                f"{replied.content[:100]}\n\n{content}"
            )
        except Exception:
            pass

    if not content and not files:
        return

    await webhook.send(
        content=content or None,
        username=message.author.display_name,
        avatar_url=message.author.display_avatar.url,
        files=files,
        allowed_mentions=discord.AllowedMentions.none(),
    )

    await bot.process_commands(message)


@bot.event
async def on_guild_channel_update(before, after):
    """When a channel in Server A is renamed, rename its mirror in Server B."""
    if before.guild.id != SERVER_A_ID:
        return

    if before.name == after.name:
        return

    if not isinstance(after, discord.TextChannel):
        return

    guild_b = bot.get_guild(SERVER_B_ID)
    if guild_b is None:
        return

    mirror = discord.utils.get(guild_b.text_channels, name=before.name)
    if mirror is not None:
        await mirror.edit(name=after.name)


# Start the bot
bot.run(TOKEN)
