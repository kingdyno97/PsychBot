import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# ───────────────────────────────────────
# Load environment
# ───────────────────────────────────────
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    print("Missing DISCORD_TOKEN or GROQ_API_KEY")
    exit(1)

groq_client = Groq(api_key=GROQ_API_KEY)

# ───────────────────────────────────────
# Bot setup
# ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
processing_lock = asyncio.Lock()
processed_messages = deque(maxlen=5000)

cooldown = 8
last_response_time = 0

# Channel memory
bot_memory = {}

# Psychological profiles
user_profiles = {}

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()


def classify_message(text):
    prompt = f"""
Strict classifier. Return ONLY one word.

ATTACK = blatant targeted bullying
DISTRESS = clear emotional distress
NORMAL = everything else

If unsure return NORMAL.

Message:
{text}
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=5
        )

        result = r.choices[0].message.content.upper().strip()

        if "ATTACK" in result:
            return "ATTACK"

        if "DISTRESS" in result:
            return "DISTRESS"

        return "NORMAL"

    except:
        return "NORMAL"


# ───────────────────────────────────────
# Psychological profiles
# ───────────────────────────────────────
def generate_user_profile(history):

    history_text = "\n".join(history[-30:])

    prompt = f"""
Analyze the psychological behavior patterns of this Discord user.

Write a short 2-3 sentence behavioral profile.

Focus on traits like:
- sarcasm
- insecurity
- attention seeking
- defensive humor
- argumentative style

Messages:
{history_text}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=120
        )

        return clean_ai_output(r.choices[0].message.content)

    except:
        return None


async def update_user_profile(channel, user_id):

    history = []

    async for msg in channel.history(limit=300):

        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)

        if len(history) >= 40:
            break

    profile = generate_user_profile(history)

    if profile:
        user_profiles[user_id] = profile


# ───────────────────────────────────────
# AI Generators
# ───────────────────────────────────────
def generate_roast(current, recent, older, profile):

    recent_text = "\n".join(recent[-10:])
    older_text = "\n".join(older[:6])

    prompt = f"""
Write one savage but clever psychological roast.

Use humor and behavioral observations.

User profile:
{profile}

Current message:
{current}

Recent messages:
{recent_text}

Older messages:
{older_text}

Rules:
- one sentence
- dark humor allowed
- no names
- no generic insults
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=1,
            max_tokens=80
        )

        return clean_ai_output(r.choices[0].message.content)

    except:
        return None


def generate_support(text):

    prompt = f"""
Someone posted this message:

{text}

Respond with one supportive but slightly humorous sentence.

Do not say crying is good.
Do not sound like a therapist.
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=60
        )

        return clean_ai_output(r.choices[0].message.content)

    except:
        return "That looks rough — but hey, at least Discord is cheaper than therapy."


def generate_free_reply(message_text, memory, target_recent, target_older, profile):

    memory_text = "\n".join([f"{role}: {content}" for role, content in memory])

    recent_text = "\n".join(target_recent[-8:])
    older_text = "\n".join(target_older[:6])

    prompt = f"""
You are PsychBot.

Personality:
- witty
- sarcastic
- psychologically observant
- confident humor

Rules:

Always respond directly to the user's message.

Never ask the user to explain more.

If the user references a person or asks you to include someone,
you must include them in the joke.

If someone criticizes the bot,
respond sarcastically.

If someone questions the humor,
defend it confidently.

If someone posts crying emojis,
you may respond humorously but NEVER say crying is good.

Avoid therapist language.

Maintain running jokes from the conversation.

Conversation:
{memory_text}

User psychological profile:
{profile}

User message:
{message_text}

Recent messages from referenced user:
{recent_text}

Older messages:
{older_text}

Write one short paragraph response.
"""

    try:

        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=150
        )

        return clean_ai_output(r.choices[0].message.content)

    except:
        return "My brain buffer just overflowed — try that again."


# ───────────────────────────────────────
# Send response
# ───────────────────────────────────────
async def send_response(channel, target, text):

    if not text:
        return

    msg = await channel.send(f"{target.mention} {text}" if target else text)

    if channel.id not in bot_memory:
        bot_memory[channel.id] = deque(maxlen=20)

    bot_memory[channel.id].append(("bot", text))


# ───────────────────────────────────────
# Events
# ───────────────────────────────────────
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")


@bot.event
async def on_message(message):

    global last_response_time

    if message.author.bot:
        return

    if message.id in processed_messages:
        return

    processed_messages.append(message.id)

    original_text = message.content[:500]
    text_lower = original_text.lower()

    now = asyncio.get_event_loop().time()

    channel_id = message.channel.id

    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=20)

    bot_memory[channel_id].append(("user", original_text))

    channel_memory = bot_memory[channel_id]

    # ───────── criticism clapbacks

    criticism_map = {
        "ai slop": "You're AI slop.",
        "bad bot": "Bad user.",
        "dumb bot": "Dumb human.",
        "stupid bot": "Stupid question generator detected."
    }

    for phrase, reply in criticism_map.items():

        if phrase in text_lower:
            await send_response(message.channel, message.author, reply)
            return

    # ───────── humor defense

    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):

        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity — the fact you stopped to analyze it just added another punchline."
        )

        return

    # ───────── emoji distress filter

    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)

    category = classify_message(original_text)

    if emoji_only:
        category = "NORMAL"

    # ───────── mention logic

    if bot.user in message.mentions:

        targets = [m for m in message.mentions if m.id != bot.user.id]
        target = targets[0] if targets else message.author

        recent = []
        older = []

        async for msg in message.channel.history(limit=400):

            if msg.author.id == target.id and msg.content.strip():

                if len(recent) < 8:
                    recent.append(msg.content)

                elif len(older) < 6:
                    older.append(msg.content)

                if len(recent) + len(older) >= 14:
                    break

        await update_user_profile(message.channel, target.id)

        profile = user_profiles.get(target.id, "No profile yet.")

        reply = generate_free_reply(
            original_text,
            channel_memory,
            recent,
            older,
            profile
        )

        await send_response(message.channel, target, reply)

        last_response_time = now

        return

    # ───────── automatic responses

    if now - last_response_time < cooldown:
        return

    if category == "ATTACK":

        await update_user_profile(message.channel, message.author.id)

        profile = user_profiles.get(message.author.id, "")

        roast = generate_roast(
            original_text,
            [],
            [],
            profile
        )

        await send_response(message.channel, message.author, roast)

        last_response_time = now

    elif category == "DISTRESS":

        support = generate_support(original_text)

        await send_response(message.channel, message.author, support)

        last_response_time = now


# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
