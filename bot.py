import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq
from time import monotonic

# -------------------------
# ENV
# -------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    raise RuntimeError("Missing DISCORD_TOKEN or GROQ_API_KEY in environment.")

groq_client = Groq(api_key=GROQ_API_KEY)

# -------------------------
# BOT SETUP
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# GLOBAL LOCKS
# -------------------------
processed_messages = deque(maxlen=5000)
processed_message_ids = set()

chat_memory = deque(maxlen=50)

cooldown = 8
last_response_time = 0


# -------------------------
# CLEAN OUTPUT
# -------------------------
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()


def _remember_message_id(message_id):
    if message_id in processed_message_ids:
        return False
    if len(processed_messages) == processed_messages.maxlen:
        oldest = processed_messages[0]
        processed_message_ids.discard(oldest)
    processed_messages.append(message_id)
    processed_message_ids.add(message_id)
    return True


async def _groq_chat(*, prompt, temperature, max_tokens):
    def _request():
        return groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    r = await asyncio.to_thread(_request)
    return clean_ai_output(r.choices[0].message.content)


# -------------------------
# CLASSIFIER
# -------------------------
async def classify_message(text):
    prompt = f"""
Classify this Discord message.

ATTACK = bullying
DISTRESS = emotional distress
NORMAL = everything else

Return only the word.

Message:
{text}
"""

    try:
        result = (await _groq_chat(prompt=prompt, temperature=0, max_tokens=5)).upper()

        if "ATTACK" in result:
            return "ATTACK"

        if "DISTRESS" in result:
            return "DISTRESS"

        return "NORMAL"

    except Exception:
        return "NORMAL"


# -------------------------
# ROAST
# -------------------------
async def generate_roast():
    prompt = """
Generate a witty one sentence roast.

Rules
- blunt
- funny
- confident
- DO NOT include any names
"""

    try:
        return await _groq_chat(prompt=prompt, temperature=0.9, max_tokens=60)
    except Exception:
        return "I would roast harder, but you're already overcooked."


# -------------------------
# SUPPORT
# -------------------------
async def generate_support():
    prompt = """
Someone seems stressed.

Respond with one supportive sentence.
Do not include names.
"""

    try:
        return await _groq_chat(prompt=prompt, temperature=0.7, max_tokens=60)
    except Exception:
        return "That sounds rough. Breathe, hydrate, and keep moving."


# -------------------------
# EVALUATION
# -------------------------
async def generate_eval(recent, older):
    r_text = "\n".join(recent)
    o_text = "\n".join(older)

    prompt = f"""
Create a short psychological observation.

Rules
- 2 sentences
- neutral tone
- no names

Recent:
{r_text}

Older:
{o_text}
"""

    try:
        return await _groq_chat(prompt=prompt, temperature=0.7, max_tokens=120)
    except Exception:
        return "Not enough stable signal yet. Need more messages for a fair read."


# -------------------------
# SEND MESSAGE
# -------------------------
async def send_response(channel, target, text):
    if not text:
        text = "My brain just blue-screened."
    if target:
        await channel.send(f"{target.mention} {text}")
    else:
        await channel.send(text)


# -------------------------
# READY
# -------------------------
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")


# -------------------------
# MESSAGE EVENT
# -------------------------
@bot.event
async def on_message(message):
    global last_response_time

    if message.author.bot:
        return

    if not _remember_message_id(message.id):
        return

    text = message.content.lower()
    now = monotonic()

    # -------------------------
    # COMMANDS (Bot Mentioned)
    # -------------------------
    if bot.user in message.mentions:
        # REMOVE BOT FROM TARGET LIST
        targets = [m for m in message.mentions if m.id != bot.user.id]
        target = targets[0] if targets else message.author

        if "roast" in text:
            roast = await generate_roast()
            await send_response(message.channel, target, roast)
            last_response_time = now
            await bot.process_commands(message)
            return

        if "eval" in text or "evaluate" in text:
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

            result = await generate_eval(recent, older)
            await send_response(message.channel, target, result)
            last_response_time = now
            await bot.process_commands(message)
            return

        await send_response(message.channel, message.author, "Mention me with `roast` or `eval`.")
        await bot.process_commands(message)
        return

    # -------------------------
    # COOLDOWN
    # -------------------------
    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    # -------------------------
    # AUTO DETECTION
    # -------------------------
    category = await classify_message(text)

    if category == "ATTACK":
        roast = await generate_roast()
        await send_response(message.channel, message.author, roast)
        last_response_time = now
        await bot.process_commands(message)
        return

    if category == "DISTRESS":
        support = await generate_support()
        await send_response(message.channel, message.author, support)
        last_response_time = now
        await bot.process_commands(message)
        return

    await bot.process_commands(message)


# -------------------------
# RUN BOT
# -------------------------
bot.run(DISCORD_TOKEN)
