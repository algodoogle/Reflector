import discord
from discord.ext import commands

# Your bot token (keep this secret)
TOKEN = "TOKEN"

# Channel IDs
SOURCE_CHANNEL_ID = 1516388832134566048  # Channel to mirror FROM
DESTINATION_CHANNEL_ID = 1516388869698879528  # Channel to mirror TO

# Enable required intents (message content is required for reading messages)
intents = discord.Intents.default()
intents.message_content = True

# Create bot instance
bot = commands.Bot(command_prefix="!", intents=intents)

# Cache webhooks so we don't recreate/fetch them every message
webhook_cache = {}


async def get_webhook(channel: discord.TextChannel):
    """
    Get or create a webhook for the destination channel.
    Webhooks allow us to impersonate users (name + avatar).
    """

    # Return cached webhook if already stored
    if channel.id in webhook_cache:
        return webhook_cache[channel.id]

    # Fetch existing webhooks in the channel
    webhooks = await channel.webhooks()

    # Try to find one we already created
    webhook = discord.utils.get(webhooks, name="MirrorWebhook")

    # If none exists, create one
    if webhook is None:
        webhook = await channel.create_webhook(name="MirrorWebhook")

    # Cache it for future use
    webhook_cache[channel.id] = webhook
    return webhook


@bot.event
async def on_message(message: discord.Message):
    """
    Triggered whenever a message is sent in any channel the bot can see.
    We filter it down to only mirror one specific channel.
    """

    # Ignore bot messages (prevents loops and spam)
    if message.author.bot:
        return

    # Only mirror messages from the source channel
    if message.channel.id != SOURCE_CHANNEL_ID:
        return

    # Get destination channel object
    destination_channel = bot.get_channel(DESTINATION_CHANNEL_ID)
    if destination_channel is None:
        return

    # Get or create webhook in destination channel
    webhook = await get_webhook(destination_channel)

    # Prepare attachments (images, files, etc.)
    files = []
    for attachment in message.attachments:
        file = await attachment.to_file()
        files.append(file)

    # Start with raw message content
    content = message.content

    # If message is a reply, include context of what it replied to
    if message.reference:
        try:
            replied = await message.channel.fetch_message(
                message.reference.message_id
            )

            # Prepend simple reference info
            content = (
                f"> Replying to {replied.author.display_name}: "
                f"{replied.content[:100]}\n\n{content}"
            )
        except Exception:
            # If message can't be fetched, just ignore reply context
            pass

    # Send mirrored message using webhook
    await webhook.send(
        content=content,
        username=message.author.display_name,  # Show original nickname
        avatar_url=message.author.display_avatar.url,  # Show profile picture
        files=files,
        allowed_mentions=discord.AllowedMentions.none()  # Prevent ping abuse
    )

    # Let other commands still work if you add any
    await bot.process_commands(message)


# Start the bot
bot.run(TOKEN)