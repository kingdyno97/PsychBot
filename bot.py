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
    print("Missing DISCORD_TOKEN or GROQ_API_KEY in environment variables")
    exit(1)

groq_client = Groq(api_key=GROQ_API_KEY)

# ───────────────────────────────────────
# Bot setup – MUST COME FIRST
# ───────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
processing_lock = asyncio.Lock()
processed_messages = set()
chat_memory = deque(maxlen=50)
cooldown = 8
last_response_time = 0

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()

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
    except Exception as e:
        print(f"Classification error: {e}")
        return "NORMAL"

def generate_roast():
    prompt = """
Generate a witty one sentence roast.

Rules
- blunt
- funny
- confident
- DO NOT include any names
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=60
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Roast generation error: {e}")
        return "Couldn't generate a roast right now."

def generate_support():
    prompt = """
Someone seems stressed.

Respond with one supportive sentence.
Do not include names.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=60
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Support generation error: {e}")
        return "Hey, take a breath — you're not alone."

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
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=120
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Eval generation error: {e}")
        return "Not enough data to evaluate."

async def send_response(channel, target, text):
    try:
        await channel.send(f"{target.mention} {text}")
    except Exception as e:
        print(f"Send response error: {e}")

# ───────────────────────────────────────
# Events – MUST COME AFTER bot definition
# ───────────────────────────────────────
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")

@bot.event
async def on_message(message):
    global last_response_time

    if message.author.bot:
        return

    async with processing_lock:
        if message.id in processed_messages:
            return
        processed_messages.add(message.id)

        text = message.content.lower()
        now = asyncio.get_event_loop().time()

        # Bot mentioned → prioritize diagnose/evaluate
        if bot.user in message.mentions:
            targets = [m for m in message.mentions if m.id != bot.user.id]
            if not targets:
                return
            target = targets[0]

            content_lower = message.content.lower()

            # Diagnose / evaluate keywords – CHECK FIRST
            diagnose_keywords = ["diagnose", "evaluate", "eval", "psych", "analysis", "assess"]
            if any(word in content_lower for word in diagnose_keywords):
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

                if not recent and not older:
                    await message.channel.send(f"{target.mention} No recent messages found to evaluate.")
                    return

                result = generate_eval(recent, older)
                await send_response(message.channel, target, result)
                last_response_time = now
                return  # ← CRITICAL: STOP HERE – no roast, no further processing

            # Only reach roast if NO diagnose keyword was found
            if "roast" in content_lower:
                roast = generate_roast()
                await send_response(message.channel, target, roast)
                last_response_time = now
                return

        # Cooldown for auto-responses
        if now - last_response_time < cooldown:
            await bot.process_commands(message)
            return

        # Auto detection (non-mentioned messages)
        category = classify_message(text)

        if category == "ATTACK":
            roast = generate_roast()
            await send_response(message.channel, message.author, roast)
            last_response_time = now

        elif category == "DISTRESS":
            support = generate_support()
            await send_response(message.channel, message.author, support)
            last_response_time = now

        # Always process prefix ! commands at the very end
        await bot.process_commands(message)

# ───────────────────────────────────────
# Run bot
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
