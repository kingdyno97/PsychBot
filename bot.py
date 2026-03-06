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
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
processing_lock = asyncio.Lock()
processed_messages = set()
cooldown = 8
last_response_time = 0

# Memory: last 10 bot replies in this channel (for callbacks)
bot_memory = deque(maxlen=10)  # stores (channel_id, message_content) tuples

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

ATTACK = blatant targeted bullying (kill yourself, mock disability/trauma/race/sexuality cruelly)
DISTRESS = clear suicide/distress/cry for help
NORMAL = everything else (group banter, casual swearing, jokes, sarcasm, compliments, bot-teasing)

If in doubt, return NORMAL.

Message:
{text}
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
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

def generate_roast(target_recent, target_older):
    recent = "\n".join(target_recent[-12:]) if target_recent else "No recent messages."
    older = "\n".join(target_older[:8]) if target_older else ""

    prompt = f"""
Dark, witty psychological roaster. Make it entertaining.

Analyze history. Find unique patterns: projection, deflection, narcissism, daddy/mommy issues, avoidance, control obsession, etc.
Deliver ONE savage, hilarious sentence that hits their core wound in a fresh, funny way.

Rules:
- Be clever, dark humor OK
- NO repeating tropes (attention seeking, mask, cry for help)
- NO shallow burns (looks, clothes, skills)
- NO names
- One sentence only
- Always original and surprising

Recent:
{recent}

Older:
{older}
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=1.0,
            max_tokens=90
        )
        return clean_ai_output(r.choices[0].message.content)
    except:
        return None

def generate_support(text):
    prompt = f"""
Someone seems distressed (message: "{text}").

Reply with one kind sentence that has dark, sarcastic humor but is still supportive.
No names.
Keep it light but real.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=60
        )
        return clean_ai_output(r.choices[0].message.content)
    except:
        return "Damn, that hit deep. Take a breath — you're still here, so the darkness hasn't won yet... yet."

def generate_eval(recent, older):
    r_text = "\n".join(recent[-8:]) if recent else ""
    o_text = "\n".join(older[:6]) if older else ""

    if len(recent) + len(older) < 3:
        return "Not enough message history to diagnose properly — the target is either a ghost or wisely staying quiet."

    prompt = f"""
Short psychological observation (exactly 2 sentences).

Neutral, clinical tone.
No names.
Focus on patterns, projection, deflection, narcissism, attachment issues, coping mechanisms.

Recent:
{r_text}

Older:
{o_text}
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=120
        )
        return clean_ai_output(r.choices[0].message.content)
    except:
        return "Insufficient data for meaningful evaluation."

def generate_free_reply(question, memory_context):
    memory_str = "\n".join(memory_context) if memory_context else "No prior bot replies in this channel."

    prompt = f"""
You are PsychBot — edgy, sarcastic, psychologically sharp, darkly funny AI.

The user just said: "{question}"

Previous things you've said in this channel (reference if relevant):
{memory_str}

Reply naturally — answer questions, react to statements, be witty/sarcastic/insightful.
Keep it entertaining, a bit mean if it fits, but never cruel for no reason.
One short paragraph max unless it's deep.
No names unless directly addressing.
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=150
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Free reply error: {e}")
        return "Wow, deep question. My circuits are shorting out from how profound that was. Try again?"

async def send_response(channel, target, text):
    if text is None:
        return
    try:
        msg = await channel.send(f"{target.mention} {text}" if target else text)
        # Store bot's own reply in memory
        bot_memory.append((channel.id, text))
        return msg
    except Exception as e:
        print(f"Send error: {e}")

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

    async with processing_lock:
        if message.id in processed_messages:
            return
        processed_messages.add(message.id)

        text = message.content.lower()
        now = asyncio.get_event_loop().time()

        channel_memory = [m[1] for m in bot_memory if m[0] == message.channel.id]

        # Bot mentioned → special commands first
        if bot.user in message.mentions:
            targets = [m for m in message.mentions if m.id != bot.user.id]
            target = targets[0] if targets else None

            content_lower = message.content.lower()

            eval_keywords = ["diagnose", "evaluate", "eval", "psych", "analysis", "assess", "psychological"]
            if any(kw in content_lower for kw in eval_keywords):
                recent = []
                older = []
                async for msg in message.channel.history(limit=400):
                    if msg.author.id == (target.id if target else message.author.id) and msg.content.strip():
                        if len(recent) < 8:
                            recent.append(msg.content)
                        elif len(older) < 6:
                            older.append(msg.content)
                        if len(recent) + len(older) >= 14:
                            break

                result = generate_eval(recent, older)
                await send_response(message.channel, target or message.author, result)
                last_response_time = now
                return

            if "roast" in content_lower:
                recent = []
                older = []
                async for msg in message.channel.history(limit=400):
                    if msg.author.id == (target.id if target else message.author.id) and msg.content.strip():
                        if len(recent) < 8:
                            recent.append(msg.content)
                        elif len(older) < 6:
                            older.append(msg.content)
                        if len(recent) + len(older) >= 14:
                            break

                roast = generate_roast(recent, older)
                if roast:
                    await send_response(message.channel, target or message.author, roast)
                last_response_time = now
                return

            # Free-form reply if no command matched
            reply = generate_free_reply(message.content, channel_memory)
            await send_response(message.channel, target or message.author, reply)
            last_response_time = now
            return

        # Auto-detection – ONLY blatant ATTACK
        if now - last_response_time < cooldown:
            await bot.process_commands(message)
            return

        category = classify_message(text)

        if category == "ATTACK":
            recent = []
            older = []
            async for msg in message.channel.history(limit=400):
                if msg.author.id == message.author.id and msg.content.strip():
                    if len(recent) < 8:
                        recent.append(msg.content)
                    elif len(older) < 6:
                        older.append(msg.content)
                    if len(recent) + len(older) >= 14:
                        break

            roast = generate_roast(recent, older)
            if roast:
                await send_response(message.channel, message.author, roast)
            last_response_time = now

        elif category == "DISTRESS":
            support = generate_support()
            if support:
                await send_response(message.channel, message.author, support)
            last_response_time = now

        await bot.process_commands(message)

# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
