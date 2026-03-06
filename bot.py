import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq
import random

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
# Discord setup
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
bot_memory = {}              # channel_id -> deque of (role, content)
user_profiles = {}           # user_id -> profile

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()

# Classify messages as ATTACK / DISTRESS / NORMAL
def classify_message(text):
    prompt = f"""
Return ONLY one word.

ATTACK = targeted bullying
DISTRESS = emotional distress
NORMAL = everything else

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
        result = r.choices[0].message.content.upper()
        if "ATTACK" in result:
            return "ATTACK"
        if "DISTRESS" in result:
            return "DISTRESS"
        return "NORMAL"
    except:
        return "NORMAL"

# Generate psychological profile for a user
def generate_user_profile(messages):
    history = "\n".join(messages[-30:])
    prompt = f"""
Analyze the behavioral patterns of this Discord user.
Write a short 2 sentence psychological observation.

Messages:
{history}
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

# AI free-form reply
def generate_free_reply(message_text, memory, target_recent, target_older, profile):
    memory_text = "\n".join([f"{role}: {content}" for role, content in memory])
    recent_text = "\n".join(target_recent[-8:]) if target_recent else ""
    older_text = "\n".join(target_older[:6]) if target_older else ""
    prompt = f"""
You are PsychBot.

Personality:
- witty
- sarcastic
- psychologically observant
- dark humor

Rules:
- Always respond directly to the user's request
- Never ask them to explain more
- If a user tells you to include someone in a joke, include them
- If someone criticizes the bot, respond sarcastically
- If someone questions humor, defend it confidently
- If someone posts crying emojis, you may react but NEVER say crying is good
- Avoid therapy language
- Continue running jokes if they exist in the conversation

Conversation history:
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
    except Exception as e:
        print("AI ERROR:", e)
        return "My brain just blue-screened."

# Supportive response for distress
def generate_support(text):
    prompt = f"""
Someone posted this:

{text}

Reply with one supportive but slightly humorous sentence.
Do NOT say crying is good.
Do NOT sound like a therapist.
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

# Send response and save in memory
async def send_response(channel, target, text):
    if not text:
        text = "My brain just blue-screened."
    msg = await channel.send(f"{target.mention} {text}" if target else text)
    if channel.id not in bot_memory:
        bot_memory[channel.id] = deque(maxlen=20)
    bot_memory[channel.id].append(("bot", text))

# ───────────────────────────────────────
# Redirected roast for trivial/hypothetical requests
# ───────────────────────────────────────
def generate_redirected_roast(text):
    responses = [
        "So instead of helping the guy, everyone decided the best move was asking a robot to roast him. That says more about you than it does about him.",
        "Interesting strategy. Someone might be struggling and the group solution is outsourcing the bullying to a bot.",
        "Maybe the real roast here is the group of people who needed an AI to insult someone for them.",
        "Wild how fast people will mock someone instead of helping them. And somehow a robot got dragged into it.",
        "If the goal was to expose narcissism, asking a bot to roast someone over trivial stuff is a pretty efficient way to do it."
    ]
    return random.choice(responses)

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
    print(f"MESSAGE: {message.author}: {message.content}")
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

    # ───── Criticism triggers ─────
    criticism_triggers = ["ai slop", "ai garbage", "bad bot", "dumb bot", "stupid bot"]
    if any(trigger in text_lower for trigger in criticism_triggers):
        await send_response(message.channel, message.author, "You're AI slop.")
        print("Triggered: criticism response")
        return

    # ───── Humor defense ─────
    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity — the fact you stopped to analyze it just added another punchline."
        )
        return

    # ───── Kitchen table / roast redirect triggers ─────
    roast_triggers = ["roast", "make fun of", "mock", "insult", "clown on", "destroy him"]
    kitchen_triggers = ["kitchen table", "nonexistent table", "no table", "imaginary table"]

    if any(trigger in text_lower for trigger in roast_triggers + kitchen_triggers):
        response = generate_redirected_roast(original_text)
        await send_response(message.channel, message.author, response)
        print("Triggered: redirected roast")
        return

    # ───── Emoji distress filter ─────
    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)
    category = classify_message(original_text)
    if emoji_only:
        category = "NORMAL"

    # ───── Mention / AI reply ─────
    mentioned = bot.user in message.mentions or any(w in text_lower for w in ["psychbot", "bot", "table", "roast"])
    if mentioned:
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
            original_text, channel_memory, recent, older, profile
        )
        await send_response(message.channel, target, reply)
        last_response_time = now
        return

    # ───── Automatic distress responses ─────
    if bot.user not in message.mentions and now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    if category == "DISTRESS":
        support = generate_support(original_text)
        await send_response(message.channel, message.author, support)
        last_response_time = now

    await bot.process_commands(message)

# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
