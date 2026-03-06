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
channel_last_response_time = {}  # channel_id -> monotonic timestamp

bot_memory = {}  # channel_id -> deque[(role, content)]
user_profiles = {}  # user_id -> profile
profile_updated_at = {}  # user_id -> monotonic time
pending_profile_updates = set()

PROFILE_REFRESH_SECONDS = 300
GROQ_TIMEOUT_SECONDS = 30
GROQ_CONCURRENCY = 3
GROQ_RETRIES = 2
groq_semaphore = asyncio.Semaphore(GROQ_CONCURRENCY)

configured_model = os.getenv("GROQ_MODEL")
MODEL_CANDIDATES = [m for m in [
    configured_model,
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
] if m]

active_model = None
groq_error_count = 0
last_groq_error = ""
SCRIPT_VERSION = "psychbot-2026-03-06-r3"


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
    global active_model, groq_error_count, last_groq_error

    candidate_models = []
    if active_model:
        candidate_models.append(active_model)
    for m in MODEL_CANDIDATES:
        if m not in candidate_models:
            candidate_models.append(m)

    if not candidate_models:
        raise RuntimeError("No Groq model candidates configured.")

    def _request(model_name: str):
        return groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    last_error = None
    for model_name in candidate_models:
        for attempt in range(GROQ_RETRIES + 1):
            try:
                async with groq_semaphore:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(_request, model_name),
                        timeout=GROQ_TIMEOUT_SECONDS,
                    )
                active_model = model_name
                return clean_ai_output(response.choices[0].message.content)
            except Exception as e:
                last_error = e
                groq_error_count += 1
                last_groq_error = f"{type(e).__name__}: {e}"
                err = str(e).lower()

                # If model is invalid/deprecated, move to next model immediately.
                if ("model" in err and "not" in err and "found" in err) or "deprecat" in err:
                    break

                if attempt < GROQ_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break

    raise last_error if last_error else RuntimeError("Unknown Groq error")


def channel_memory_for(channel_id: int) -> deque:
    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=20)
    return bot_memory[channel_id]


def likely_emotional_content(text_lower: str) -> bool:
    distress_cues = [
        "sad",
        "depressed",
        "depression",
        "anxious",
        "anxiety",
        "stressed",
        "stress",
        "hate my life",
        "i'm done",
        "i am done",
        "can't do this",
        "cant do this",
        "crying",
        "overwhelmed",
        "lonely",
        "hurt",
        "panic",
        "panic attack",
        "suicidal",
        "kill myself",
    ]
    attack_cues = [
        "idiot",
        "stupid",
        "dumb",
        "loser",
        "trash",
        "worthless",
        "kys",
    ]
    return any(cue in text_lower for cue in distress_cues + attack_cues)


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


async def refresh_profile_background(channel, user_id: int):
    if user_id in pending_profile_updates:
        return
    pending_profile_updates.add(user_id)
    try:
        await update_user_profile(channel, user_id)
    except Exception as e:
        print(f"refresh_profile_background error for {user_id}:", e)
    finally:
        pending_profile_updates.discard(user_id)


def local_fallback_reply(message_text: str, roast_target_name=None, requester_name=None) -> str:
    text_lower = message_text.lower()
    if roast_target_name and requester_name:
        return (
            f"{roast_target_name} talks like a loading screen, "
            f"and {requester_name} outsourced the punchline."
        )
    if "roast" in text_lower or "mock" in text_lower or "insult" in text_lower:
        return "You asked for heat, so here it is: loud confidence is still not personality."
    return random.choice(
        [
            "That message had energy; now give it direction.",
            "You brought chaos, I brought structure.",
            "Noted. Continue.",
        ]
    )


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
        return local_fallback_reply(message_text, roast_target_name, requester_name)


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
    text = clean_ai_output(text) if text else "Say that again and I will answer properly."
    await channel.send(f"{target.mention} {text}" if target else text, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    channel_memory_for(channel.id).append(("bot", text))


# ───────────────────────────────────────
# Discord Events
# ───────────────────────────────────────
@bot.event
async def on_ready():
    candidate_list = ", ".join(MODEL_CANDIDATES)
    print(f"PsychBot online: {bot.user} | version={SCRIPT_VERSION} | model_candidates=[{candidate_list}]")


@bot.command(name="health")
async def health(ctx):
    model = active_model or "none-yet"
    err = last_groq_error if last_groq_error else "none"
    await ctx.send(
        f"version={SCRIPT_VERSION} model={model} groq_errors={groq_error_count} last_error={err}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.event
async def on_message(message):
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
    channel_id = message.channel.id
    last_response_time = channel_last_response_time.get(channel_id, 0.0)

    channel_memory = channel_memory_for(channel_id)
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
            profile = user_profiles.get(targets[0].id, "No profile yet.")
            asyncio.create_task(refresh_profile_background(message.channel, targets[0].id))
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
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # ───── Mention / AI reply ─────
    mentioned = bot.user in message.mentions or "psychbot" in text_lower
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

        profile = user_profiles.get(target.id, "No profile yet.")
        asyncio.create_task(refresh_profile_background(message.channel, target.id))

        reply = await generate_free_reply(original_text, channel_memory, recent, older, profile)
        await send_response(message.channel, target, reply)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # ───── Automatic distress responses ─────
    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)
    if emoji_only:
        category = "NORMAL"
    elif likely_emotional_content(text_lower):
        category = await classify_message(original_text)
    else:
        category = "NORMAL"

    if category == "ATTACK":
        quick_roast = random.choice(
            [
                "If confidence matched your logic, we'd all be doomed.",
                "You swing hard for someone missing the target this badly.",
                "That was loud, not smart.",
            ]
        )
        await send_response(message.channel, message.author, quick_roast)
        channel_last_response_time[channel_id] = now
    elif category == "DISTRESS":
        support = await generate_support(original_text)
        await send_response(message.channel, message.author, support)
        channel_last_response_time[channel_id] = now

    await bot.process_commands(message)


# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
