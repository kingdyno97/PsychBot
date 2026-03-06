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
processed_messages = deque(maxlen=5000)
cooldown = 8
last_response_time = 0
bot_memory = {}  # channel_id -> deque of (role, content)
user_profiles = {}  # user_id -> profile

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()

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

# ───────────── AI Free-form Reply ─────────────
def generate_free_reply(message_text, memory, target_recent, target_older, profile,
                        requester_name=None, roast_target_name=None, long_reply=False):
    """
    long_reply: True for roast / evaluation / analysis commands
    Always use display_name only; never IDs; never speak for someone else
    """
    memory_text = "\n".join([f"{role}: {content}" for role, content in memory])
    recent_text = "\n".join(target_recent[-8:]) if target_recent else ""
    older_text = "\n".join(target_older[:6]) if target_older else ""

    roast_instructions = ""
    if roast_target_name and requester_name and long_reply:
        roast_instructions = f"""
If the user asks you to roast someone, first roast {roast_target_name} directly.
Then roast {requester_name} for asking a bot to do it.
Keep both roasts witty, short, and sarcastic.
Do not reveal user IDs.
Avoid literal child locations (playground, school, kindergarten), 
but you may roast immaturity or age humorously.
Do not speak for anyone else, only reply from the bot itself.
"""

    prompt = f"""
You are PsychBot.

Personality:
- witty
- sarcastic
- psychologically observant
- dark humor

Rules:
- Always respond directly to the author of the message
- Never ask them to explain more
- Avoid therapy language
- Never reference IDs
- Continue running jokes if they exist

Special roast behavior:
{roast_instructions}

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

Write one short paragraph response from the bot only.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.9,
            max_tokens=150 if long_reply else 60
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print("AI ERROR:", e)
        return "My brain just blue-screened."

# ───────────── Supportive Replies ─────────────
def generate_support(text):
    prompt = f"""
Someone posted this:

{text}

Reply with one supportive but slightly humorous sentence.
Do NOT say crying is good.
Do NOT sound like a therapist.
Keep it short.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=40
        )
        return clean_ai_output(r.choices[0].message.content)
    except:
        return "That looks rough — but hey, at least Discord is cheaper than therapy."

# ───────────── Send response ─────────────
async def send_response(channel, target, text):
    if not text:
        text = "My brain just blue-screened."
    msg = await channel.send(f"{target.mention} {text}" if target else text)
    if channel.id not in bot_memory:
        bot_memory[channel.id] = deque(maxlen=20)
    bot_memory[channel.id].append(("bot", text))

# ───────────── Discord Events ─────────────
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

    # ───── Criticism triggers ─────
    criticism_triggers = ["ai slop", "ai garbage", "bad bot", "dumb bot", "stupid bot"]
    if any(trigger in text_lower for trigger in criticism_triggers):
        short_responses = [
            "Me? Slop? Bold claim from a human.",
            "Careful, your opinion just got roasted instead.",
            "Wow, coming for a bot? Courageous.",
            "And here I thought humans were funny."
        ]
        await send_response(message.channel, message.author, random.choice(short_responses))
        return

    # ───── Humor defense ─────
    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity — the fact you stopped to analyze it just added another punchline."
        )
        return

    # ───── Roast / Eval / Analysis triggers ─────
    roast_keywords = ["roast", "make fun of", "mock", "insult", "clown on", "destroy him"]
    special_eval_keywords = ["evaluate", "diagnose", "analyze", "profile"]
    special_commands = roast_keywords + special_eval_keywords

    command_triggered = any(k in text_lower for k in special_commands)
    if command_triggered:
        targets = [m for m in message.mentions if m.id != bot.user.id]
        target_name = targets[0].display_name if targets else "someone"
        requester_name = message.author.display_name

        # Update profile if target exists
        if targets:
            await update_user_profile(message.channel, targets[0].id)
            profile = user_profiles.get(targets[0].id, "No profile yet.")
        else:
            profile = "No profile yet."

        reply = generate_free_reply(
            original_text,
            channel_memory,
            [], [],
            profile,
            requester_name=requester_name,
            roast_target_name=target_name,
            long_reply=True
        )
        await send_response(message.channel, message.author, reply)
        return

    # ───── Short replies for normal conversation ─────
    mentioned = bot.user in message.mentions or any(w in text_lower for w in ["psychbot", "bot"])
    if mentioned:
        reply = generate_free_reply(
            original_text,
            channel_memory,
            [], [],
            profile="No profile yet.",
            long_reply=False
        )
        await send_response(message.channel, message.author, reply)
        last_response_time = now
        return

    # ───── Emoji distress filter ─────
    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)
    category = classify_message(original_text)
    if emoji_only:
        category = "NORMAL"

    if category == "DISTRESS":
        support = generate_support(original_text)
        await send_response(message.channel, message.author, support)
        last_response_time = now

    await bot.process_commands(message)

# ───────────────────────────────────────
# Run bot
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
