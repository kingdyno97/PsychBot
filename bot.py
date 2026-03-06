import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# -------------------------
# ENV
# -------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

# -------------------------
# BOT SETUP
# -------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# GLOBAL LOCKS
# -------------------------
processing_lock = asyncio.Lock()
processed_messages = set()

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


# -------------------------
# CLASSIFIER
# -------------------------
def classify_message(text):

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

        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0,
            max_tokens=5
        )

        result = r.choices[0].message.content.upper()

        if "ATTACK" in result:
            return "ATTACK"

        if "DISTRESS" in result:
            return "DISTRESS"

        return "NORMAL"

    except:
        return "NORMAL"


# -------------------------
# ROAST
# -------------------------
def generate_roast():

    prompt = """
Generate a witty one sentence roast.

Rules
- blunt
- funny
- confident
- DO NOT include any names
"""

    r = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=60
    )

    return clean_ai_output(r.choices[0].message.content)


# -------------------------
# SUPPORT
# -------------------------
def generate_support():

    prompt = """
Someone seems stressed.

Respond with one supportive sentence.
Do not include names.
"""

    r = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=60
    )

    return clean_ai_output(r.choices[0].message.content)


# -------------------------
# EVALUATION
# -------------------------
def generate_eval(recent, older):

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

    r = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=120
    )

    return clean_ai_output(r.choices[0].message.content)


# -------------------------
# SEND MESSAGE
# -------------------------
async def send_response(channel, target, text):

    await channel.send(f"{target.mention} {text}")


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

    async with processing_lock:

        # Stop duplicates
        if message.id in processed_messages:
            return

        processed_messages.add(message.id)

        text = message.content.lower()
        now = asyncio.get_event_loop().time()

        # -------------------------
        # COMMANDS (Bot Mentioned)
        # -------------------------
        if bot.user in message.mentions:

            # REMOVE BOT FROM TARGET LIST
            targets = [
                m for m in message.mentions
                if m.id != bot.user.id
            ]

            if not targets:
                return

            target = targets[0]

            if "roast" in text:

                roast = generate_roast()

                await send_response(message.channel, target, roast)
                return

            if "eval" in text or "evaluate" in text:

                recent = []
                older = []

                async for msg in message.channel.history(limit=400):

                    if msg.author.id == target.id:

                        if len(recent) < 8:
                            recent.append(msg.content)

                        elif len(older) < 6:
                            older.append(msg.content)

                    if len(recent) + len(older) >= 14:
                        break

                result = generate_eval(recent, older)

                await send_response(message.channel, target, result)
                return


        # -------------------------
        # COOLDOWN
        # -------------------------
        if now - last_response_time < cooldown:
            return


        # -------------------------
        # AUTO DETECTION
        # -------------------------
        category = classify_message(text)

        if category == "ATTACK":

            roast = generate_roast()

            await send_response(message.channel, message.author, roast)

            last_response_time = now
            return


        if category == "DISTRESS":

            support = generate_support()

            await send_response(message.channel, message.author, support)

            last_response_time = now
            return


# -------------------------
# RUN BOT
# -------------------------
bot.run(DISCORD_TOKEN)
