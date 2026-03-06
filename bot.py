import os
import random
import asyncio
from collections import deque
from time import monotonic

import discord
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
    raise RuntimeError("Missing DISCORD_TOKEN or GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

# ───────────────────────────────────────
# Discord setup
# ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
processed_messages = deque(maxlen=5000)
processed_message_ids = set()

cooldown = 8
last_response_time = 0.0

bot_memory = {}  # channel_id -> deque[(role, content)]
user_profiles = {}  # user_id -> profile
profile_updated_at = {}  # user_id -> monotonic time

PROFILE_REFRESH_SECONDS = 300
GROQ_TIMEOUT_SECONDS = 12
GROQ_CONCURRENCY = 3
groq_semaphore = asyncio.Semaphore(GROQ_CONCURRENCY)


# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text: str) -> str:
    if not text:
        return ""
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()


def remember_message_id(message_id: int) -> bool:
    if message_id in processed_message_ids:
        return False
    if len(processed_messages) == processed_messages.maxlen:
        oldest = processed_messages[0]
        processed_message_ids.discard(oldest)
    processed_messages.append(message_id)
    processed_message_ids.add(message_id)
    return True


async def groq_chat(prompt: str, temperature: float, max_tokens: int) -> str:
    def _request():
        return groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    async with groq_semaphore:
        response = await asyncio.wait_for(asyncio.to_thread(_request), timeout=GROQ_TIMEOUT_SECONDS)
    return clean_ai_output(response.choices[0].message.content)


def channel_memory_for(channel_id: int) -> deque:
    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=20)
    return bot_memory[channel_id]


# ───────────────────────────────────────
# AI functions
# ───────────────────────────────────────
async def classify_message(text: str) -> str:
    prompt = f"""
Return ONLY one word.

ATTACK = targeted bullying
DISTRESS = emotional distress
NORMAL = everything else

Message:
{text}
"""
    try:
        result = (await groq_chat(prompt, temperature=0, max_tokens=5)).upper()
        if "ATTACK" in result:
            return "ATTACK"
        if "DISTRESS" in result:
            return "DISTRESS"
        return "NORMAL"
    except Exception as e:
        print("classify_message error:", e)
        return "NORMAL"


async def generate_user_profile(messages) -> str | None:
    if not messages:
        return None

    history = "\n".join(messages[-30:])
    prompt = f"""
Analyze the behavioral patterns of this Discord user.
Write a short 2 sentence psychological observation.

Messages:
{history}
"""
    try:
        return await groq_chat(prompt, temperature=0.5, max_tokens=120)
    except Exception as e:
        print("generate_user_profile error:", e)
        return None


async def update_user_profile(channel, user_id: int):
    now = monotonic()
    last_updated = profile_updated_at.get(user_id, 0.0)
    if now - last_updated < PROFILE_REFRESH_SECONDS:
        return

    history = []
    async for msg in channel.history(limit=300):
        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)
        if len(history) >= 40:
            break

    profile = await generate_user_profile(history)
    if profile:
        user_profiles[user_id] = profile
        profile_updated_at[user_id] = now


async def generate_free_reply(
    message_text,
    memory,
    target_recent,
    target_older,
    profile,
    requester_name=None,
    roast_target_name=None,
    avoid_children=True,
):
    memory_text = "\n".join([f"{role}: {content}" for role, content in memory])
    recent_text = "\n".join(target_recent[-8:]) if target_recent else ""
    older_text = "\n".join(target_older[:6]) if target_older else ""

    roast_instructions = ""
    if roast_target_name and requester_name:
        roast_instructions = f"""
If the user asks you to roast someone, first roast {roast_target_name} directly.
Then roast {requester_name} for asking a bot to do it.
Keep both roasts short, witty, and sarcastic.
Do not reveal user IDs.
"""
        if avoid_children:
            roast_instructions += (
                "Do NOT include age-related comments or anything implying the target is a child.\n"
            )

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
- Avoid therapy language
- Continue running jokes if they exist in the conversation

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

Write one short paragraph response.
"""
    try:
        return await groq_chat(prompt, temperature=0.9, max_tokens=80)
    except Exception as e:
        print("generate_free_reply error:", e)
        return "My brain just blue-screened."


async def generate_support(text: str) -> str:
    prompt = f"""
Someone posted this:

{text}

Reply with one supportive but slightly humorous sentence.
Do NOT say crying is good.
Do NOT sound like a therapist.
Keep it short.
"""
    try:
        return await groq_chat(prompt, temperature=0.7, max_tokens=40)
    except Exception as e:
        print("generate_support error:", e)
        return "That looks rough, but you're still in the game."


async def send_response(channel, target, text: str):
    text = clean_ai_output(text) if text else "My brain just blue-screened."
    await channel.send(
        f"{target.mention} {text}" if target else text,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    channel_memory_for(channel.id).append(("bot", text))


# ───────────────────────────────────────
# Discord Events
# ───────────────────────────────────────
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")


@bot.event
async def on_message(message):
    global last_response_time

    if message.author.bot:
        return

    if not remember_message_id(message.id):
        return

    original_text = (message.content or "")[:500]
    if not original_text.strip():
        await bot.process_commands(message)
        return

    text_lower = original_text.lower()
    now = monotonic()

    channel_memory = channel_memory_for(message.channel.id)
    channel_memory.append(("user", original_text))

    # ───── Criticism triggers ─────
    criticism_triggers = ["ai slop", "ai garbage", "bad bot", "dumb bot", "stupid bot"]
    if any(trigger in text_lower for trigger in criticism_triggers):
        short_responses = [
            "Me? Slop? Bold claim from a human.",
            "Careful, your opinion just got roasted instead.",
            "Wow, coming for a bot? Courageous.",
            "And here I thought humans were funny.",
        ]
        await send_response(message.channel, message.author, random.choice(short_responses))
        await bot.process_commands(message)
        return

    # ───── Humor defense ─────
    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity. Overanalyzing it just created the next punchline.",
        )
        await bot.process_commands(message)
        return

    # ───── Kitchen table / roast triggers ─────
    roast_triggers = ["roast", "make fun of", "mock", "insult", "clown on", "destroy him"]
    kitchen_triggers = ["kitchen table", "nonexistent table", "no table", "imaginary table"]

    if any(trigger in text_lower for trigger in roast_triggers + kitchen_triggers):
        targets = [m for m in message.mentions if m.id != bot.user.id]
        target_name = targets[0].display_name if targets else "someone"
        requester_name = message.author.display_name

        if targets:
            await update_user_profile(message.channel, targets[0].id)
            profile = user_profiles.get(targets[0].id, "No profile yet.")
        else:
            profile = "No profile yet."

        reply = await generate_free_reply(
            original_text,
            channel_memory,
            [],
            [],
            profile,
            requester_name=requester_name,
            roast_target_name=target_name,
            avoid_children=True,
        )
        await send_response(message.channel, message.author, reply)
        last_response_time = now
        await bot.process_commands(message)
        return

    # ───── Mention / AI reply ─────
    mentioned = bot.user in message.mentions or any(w in text_lower for w in ["psychbot", "table"])
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

        reply = await generate_free_reply(original_text, channel_memory, recent, older, profile)
        await send_response(message.channel, target, reply)
        last_response_time = now
        await bot.process_commands(message)
        return

    # ───── Automatic distress responses ─────
    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)
    category = "NORMAL" if emoji_only else await classify_message(original_text)

    if category == "ATTACK":
        quick_roast = random.choice(
            [
                "If confidence matched your logic, we'd all be doomed.",
                "You swing hard for someone missing the target this badly.",
                "That was loud, not smart.",
            ]
        )
        await send_response(message.channel, message.author, quick_roast)
        last_response_time = now
    elif category == "DISTRESS":
        support = await generate_support(original_text)
        await send_response(message.channel, message.author, support)
        last_response_time = now

    await bot.process_commands(message)


# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
