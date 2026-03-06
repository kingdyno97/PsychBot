import os
import discord
import asyncio
import random
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv

# ───────────────────────────────────────
# Load environment variables
# ───────────────────────────────────────
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

if not DISCORD_TOKEN:
    print("Missing DISCORD_TOKEN")
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
bot_memory = {}  # channel_id -> deque of (role, content)
user_profiles = {}  # user_id -> profile
processed_messages = set()  # to track processed messages

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_text(text):
    """Clean text by removing mentions and unwanted characters."""
    text = text.replace("@", "").replace("<", "").replace(">", "")
    return text.strip()

def generate_local_roast(target_name, requester_name):
    """Generate a quick local roast for both the target and the requester."""
    roasts = [
        f"{target_name}, how does it feel knowing a bot just roasted you? Not so tough now, huh?",
        f"Hey {target_name}, you just got roasted by a bot. Ouch! Better luck next time.",
        f"{requester_name}, asking a bot to roast someone? You sure you can't do that yourself? Pathetic.",
        f"Good job {requester_name}, you've successfully outsourced your bullying to a bot. You should be proud."
    ]
    return random.choice(roasts)

def generate_distress_response(text):
    """Generate a light-hearted, supportive response for distress."""
    distress_responses = [
        "That looks rough, but hey, at least Discord is cheaper than therapy!",
        "Feeling down? Don't worry, even the bots have bad days.",
        "If crying made us better, we'd all be geniuses by now, right?"
    ]
    return random.choice(distress_responses)

async def send_response(channel, target, text):
    """Send response to the channel, mentioning the user if provided."""
    if not text:
        text = "My brain just blue-screened, but I’ll try again."
    message = await channel.send(f"{target.mention} {text}" if target else text)
    if channel.id not in bot_memory:
        bot_memory[channel.id] = deque(maxlen=20)
    bot_memory[channel.id].append(("bot", text))

async def process_user_profile(channel, user_id):
    """Update the user profile with their recent messages."""
    history = []
    async for msg in channel.history(limit=50):
        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)
        if len(history) >= 30:
            break
    profile = "No profile yet."
    if history:
        profile = clean_text(" ".join(history))  # Placeholder for advanced analysis
    user_profiles[user_id] = profile

async def on_mention(message):
    """Handle mentions of the bot."""
    text_lower = message.content.lower()
    target_user = message.mentions[0] if message.mentions else None

    if target_user and target_user.id != bot.user.id:
        # If there's a target, roast them and roast the requester
        reply = generate_local_roast(target_user.display_name, message.author.display_name)
        await send_response(message.channel, message.author, reply)
    else:
        # Default response when mentioned casually
        default_responses = [
            "You summoned me? What's up?",
            "Yes, it's me, the bot you called.",
            "What's the matter, feeling too lazy to roast yourself?"
        ]
        await send_response(message.channel, message.author, random.choice(default_responses))

# ───────────────────────────────────────
# Events
# ───────────────────────────────────────
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")

@bot.event
async def on_message(message):
    """Main message processing loop."""
    if message.author.bot or message.id in processed_messages:
        return

    processed_messages.add(message.id)

    original_text = message.content
    text_lower = original_text.lower()

    # Handle distress/emotional support messages
    if any(emotion in original_text for emotion in ["😭", "😢", "🥺"]):
        distress_reply = generate_distress_response(original_text)
        await send_response(message.channel, message.author, distress_reply)
        return

    # Handle roast requests or mentions
    if "roast" in text_lower or "make fun of" in text_lower:
        await on_mention(message)
        return

    # Handle humor defense (when someone criticizes the bot's humor)
    if "how is your humor" in text_lower:
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity — your lack of appreciation is just the punchline."
        )
        return

    # Handle profile analysis request (can be extended with more complex logic)
    if "analyze" in text_lower or "diagnose" in text_lower:
        target_user = message.mentions[0] if message.mentions else message.author
        await process_user_profile(message.channel, target_user.id)
        profile = user_profiles.get(target_user.id, "No profile yet.")
        await send_response(message.channel, message.author, f"Here's the profile: {profile}")
        return

    # Default response if no specific triggers are matched
    await send_response(message.channel, message.author, "What can I do for you today?")

# ───────────────────────────────────────
# Run the bot
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
