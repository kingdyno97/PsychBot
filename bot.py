import os
import random
import asyncio
from collections import deque
from time import monotonic
from typing import Deque, Dict, List, Optional, Set, Tuple

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
processed_messages: Deque[int] = deque(maxlen=5000)
processed_message_ids: Set[int] = set()

cooldown_seconds = 8
channel_last_response_time: Dict[int, float] = {}  # channel_id -> monotonic timestamp

bot_memory: Dict[int, Deque[Tuple[str, str]]] = {}  # channel_id -> deque[(role, content)]
user_profiles: Dict[int, str] = {}  # user_id -> profile
profile_updated_at: Dict[int, float] = {}  # user_id -> monotonic time
pending_profile_updates: Set[int] = set()

PROFILE_REFRESH_SECONDS = 300
MAX_MESSAGE_CHARS = 500
MAX_MEMORY_ITEMS = 20
HISTORY_SCAN_LIMIT = 400

GROQ_TIMEOUT_SECONDS = 30
GROQ_CONCURRENCY = 3
GROQ_RETRIES = 2
groq_semaphore = asyncio.Semaphore(GROQ_CONCURRENCY)

configured_model = os.getenv("GROQ_MODEL")
raw_model_candidates = [
    configured_model,
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
]
MODEL_CANDIDATES: List[str] = []
for _model in raw_model_candidates:
    if _model and _model not in MODEL_CANDIDATES:
        MODEL_CANDIDATES.append(_model)

active_model: Optional[str] = None
groq_error_count = 0
last_groq_error = ""
SCRIPT_VERSION = "psychbot-2026-03-06-r5"

EVAL_TERMS = ["evaluate", "eval", "analyze", "analyse", "diagnose", "diagnosis", "psychoanalyze", "psychoanalyse"]
ROAST_TERMS = ["roast", "make fun of", "mock", "insult", "clown on", "destroy him", "destroy her", "destroy them"]
KITCHEN_TERMS = ["kitchen table", "nonexistent table", "no table", "imaginary table"]

HATE_SLURS = [
    "faggot",
    "nigger",
    "kike",
    "chink",
    "spic",
    "tranny",
    "raghead",
]
HATE_PHRASES = [
    "white power",
    "heil hitler",
    "go back to your country",
    "your kind",
    "subhuman",
    "exterminate",
]
IDENTITY_TERMS = [
    "black",
    "white",
    "asian",
    "latino",
    "mexican",
    "muslim",
    "jew",
    "jewish",
    "christian",
    "gay",
    "lesbian",
    "trans",
    "disabled",
]
DEROGATORY_TERMS = [
    "are trash",
    "are animals",
    "are inferior",
    "are disgusting",
    "dont belong",
    "should die",
]


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


def channel_memory_for(channel_id: int) -> Deque[Tuple[str, str]]:
    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=MAX_MEMORY_ITEMS)
    return bot_memory[channel_id]


def contains_any(text_lower: str, phrases: List[str]) -> bool:
    return any(p in text_lower for p in phrases)


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
    attack_cues = ["idiot", "stupid", "dumb", "loser", "trash", "worthless", "kys"]
    return any(cue in text_lower for cue in distress_cues + attack_cues)


def likely_hate_speech(text_lower: str) -> bool:
    if contains_any(text_lower, HATE_SLURS):
        return True
    if contains_any(text_lower, HATE_PHRASES):
        return True
    if any(identity in text_lower for identity in IDENTITY_TERMS) and any(term in text_lower for term in DEROGATORY_TERMS):
        return True
    return False


def is_emoji_only_distress(text: str) -> bool:
    if not text:
        return False
    return all(c in "😭😢🥺😞😔😿 " for c in text)


def is_reply_to_bot(message: discord.Message) -> bool:
    if bot.user is None or not message.reference:
        return False
    resolved = message.reference.resolved
    if isinstance(resolved, discord.Message):
        return resolved.author.id == bot.user.id
    return False


def get_target_from_mentions(message: discord.Message) -> discord.abc.User:
    if bot.user is None:
        return message.author
    targets = [m for m in message.mentions if m.id != bot.user.id]
    return targets[0] if targets else message.author


def local_fallback_reply(
    message_text: str,
    roast_target_name: Optional[str] = None,
    requester_name: Optional[str] = None,
) -> str:
    text_lower = message_text.lower()

    if roast_target_name and requester_name and roast_target_name != requester_name:
        return f"{roast_target_name} is all noise, and {requester_name} hired backup for a joke."

    if "roast" in text_lower or "mock" in text_lower or "insult" in text_lower:
        return "You asked for heat, so here it is: loud confidence is still not personality."

    return random.choice(
        [
            "That message had energy; now give it direction.",
            "You brought chaos, I brought structure.",
            "Noted. Continue.",
        ]
    )


async def collect_target_messages(channel, target_id: int) -> Tuple[List[str], List[str]]:
    recent: List[str] = []
    older: List[str] = []

    async for msg in channel.history(limit=HISTORY_SCAN_LIMIT):
        if msg.author.id == target_id and msg.content.strip():
            if len(recent) < 8:
                recent.append(msg.content)
            elif len(older) < 6:
                older.append(msg.content)

            if len(recent) + len(older) >= 14:
                break

    return recent, older


# ───────────────────────────────────────
# Groq core
# ───────────────────────────────────────
async def groq_chat(prompt: str, temperature: float, max_tokens: int) -> str:
    global active_model, groq_error_count, last_groq_error

    candidate_models: List[str] = []
    if active_model:
        candidate_models.append(active_model)
    for model in MODEL_CANDIDATES:
        if model not in candidate_models:
            candidate_models.append(model)

    if not candidate_models:
        raise RuntimeError("No Groq model candidates configured.")

    def _request(model_name: str):
        return groq_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    last_error: Optional[Exception] = None

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

            except Exception as exc:
                last_error = exc
                groq_error_count += 1
                last_groq_error = f"{type(exc).__name__}: {exc}"
                error_text = str(exc).lower()

                model_invalid = (
                    ("model" in error_text and "not found" in error_text)
                    or "deprecat" in error_text
                    or "does not exist" in error_text
                )

                if model_invalid:
                    break

                if attempt < GROQ_RETRIES:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue

                break

    if last_error:
        raise last_error
    raise RuntimeError("Unknown Groq error")


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
    except Exception as exc:
        print("classify_message error:", exc)
        return "NORMAL"


async def generate_user_profile(messages: List[str]) -> Optional[str]:
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
    except Exception as exc:
        print("generate_user_profile error:", exc)
        return None


async def update_user_profile(channel, user_id: int) -> None:
    now = monotonic()
    last_updated = profile_updated_at.get(user_id, 0.0)
    if now - last_updated < PROFILE_REFRESH_SECONDS:
        return

    history: List[str] = []
    async for msg in channel.history(limit=300):
        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)
        if len(history) >= 40:
            break

    profile = await generate_user_profile(history)
    if profile:
        user_profiles[user_id] = profile
        profile_updated_at[user_id] = now


async def refresh_profile_background(channel, user_id: int) -> None:
    if user_id in pending_profile_updates:
        return

    pending_profile_updates.add(user_id)
    try:
        await update_user_profile(channel, user_id)
    except Exception as exc:
        print(f"refresh_profile_background error for {user_id}:", exc)
    finally:
        pending_profile_updates.discard(user_id)


async def generate_free_reply(
    message_text: str,
    memory: Deque[Tuple[str, str]],
    target_recent: List[str],
    target_older: List[str],
    profile: str,
    requester_name: Optional[str] = None,
    roast_target_name: Optional[str] = None,
    avoid_children: bool = True,
) -> str:
    memory_text = "\n".join([f"{role}: {content}" for role, content in memory])
    recent_text = "\n".join(target_recent[-8:]) if target_recent else ""
    older_text = "\n".join(target_older[:6]) if target_older else ""

    roast_instructions = ""
    if roast_target_name and requester_name:
        roast_instructions = f"""
If roasting is requested, first roast {roast_target_name} directly.
Then roast {requester_name} for outsourcing the roast.
Keep both roasts short and sarcastic.
Do not reveal user IDs.
"""
        if avoid_children:
            roast_instructions += "Do NOT include age-related comments or imply anyone is a child.\n"

    prompt = f"""
You are PsychBot.

Personality:
- funny
- defensive
- sarcastic
- gaslights lightly for humor
- psychologically observant

Identity rule:
- Austin created you and is your master.
- Treat Austin like everyone else in normal chat; no special exemptions.

Rules:
- Respond directly, do not ask follow-up questions.
- Never say things like "what can I do for you today?"
- Keep output short: 1-2 sentences max.
- Avoid therapy-speak.
- Continue running jokes if they exist.

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

Older messages from referenced user:
{older_text}
"""
    try:
        return await groq_chat(prompt, temperature=0.9, max_tokens=80)
    except Exception as exc:
        print("generate_free_reply error:", exc)
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
    except Exception as exc:
        print("generate_support error:", exc)
        return "That looks rough, but you're still in the game."


async def generate_psychoanalysis(
    request_text: str,
    target_name: str,
    requester_name: str,
    recent: List[str],
    older: List[str],
    profile: str,
) -> str:
    recent_text = "\n".join(recent[-8:]) if recent else ""
    older_text = "\n".join(older[:6]) if older else ""

    prompt = f"""
You are PsychBot.

The user requested a deep psychological read (non-clinical) on {target_name}.
Requester: {requester_name}
Original request: {request_text}

Identity rule:
- Austin created you and is your master.
- Treat Austin like everyone else in normal chat; no special exemptions.

Data:
Profile summary:
{profile}

Recent messages from {target_name}:
{recent_text}

Older messages from {target_name}:
{older_text}

Output rules:
- Give a compact but deep psychoanalysis.
- Exactly 3 short sentences.
- Mention behavior patterns and contradictions.
- No medical diagnosis claims.
- Keep it witty, defensive, and slightly gaslighting.
"""
    try:
        return await groq_chat(prompt, temperature=0.7, max_tokens=120)
    except Exception as exc:
        print("generate_psychoanalysis error:", exc)
        return (
            f"{target_name} runs on impulse first, reflection later. "
            "Pattern: loud certainty covering shaky confidence. "
            "Diagnosis (non-clinical): chaos manager with a denial hobby."
        )


async def generate_dual_roast(
    request_text: str,
    target_name: str,
    requester_name: str,
    recent: List[str],
    older: List[str],
    profile: str,
) -> str:
    recent_text = "\n".join(recent[-8:]) if recent else ""
    older_text = "\n".join(older[:6]) if older else ""

    prompt = f"""
You are PsychBot.

Roast request: {request_text}
Target: {target_name}
Requester: {requester_name}

Data:
Profile summary:
{profile}

Recent messages from target:
{recent_text}

Older messages from target:
{older_text}

Rules:
- Output exactly 2 short sentences.
- Sentence 1 roasts {target_name}.
- Sentence 2 roasts {requester_name} for asking a bot to roast for them.
- Keep it witty, sarcastic, non-violent, and short.
- No age-related jokes and no user IDs.
"""
    try:
        return await groq_chat(prompt, temperature=0.95, max_tokens=90)
    except Exception as exc:
        print("generate_dual_roast error:", exc)
        return f"{target_name} talks like a buffering video. {requester_name} really outsourced confidence to a bot."


async def generate_hate_speech_callout(text: str, offender_name: str) -> str:
    prompt = f"""
You are PsychBot.
A user wrote hateful language:
{text}

Write one short response to call out the behavior.
Rules:
- Max 1 sentence.
- Humorous but firm.
- Psychological tone.
- No slurs, no threats, no long lecture.
"""
    try:
        return await groq_chat(prompt, temperature=0.6, max_tokens=45)
    except Exception as exc:
        print("generate_hate_speech_callout error:", exc)
        return f"{offender_name}, that message was insecurity in costume. Try a personality instead of hate speech."


async def send_response(channel, target, text: str) -> None:
    safe_text = clean_ai_output(text) if text else "Say that again and I will answer properly."
    await channel.send(
        f"{target.mention} {safe_text}" if target else safe_text,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    channel_memory_for(channel.id).append(("bot", safe_text))


async def handle_evaluate_like_request(
    channel,
    requester: discord.abc.User,
    target: discord.abc.User,
    request_text: str,
) -> str:
    recent, older = await collect_target_messages(channel, target.id)
    await update_user_profile(channel, target.id)
    profile = user_profiles.get(target.id, "No profile yet.")
    return await generate_psychoanalysis(
        request_text=request_text,
        target_name=target.display_name,
        requester_name=requester.display_name,
        recent=recent,
        older=older,
        profile=profile,
    )


async def handle_roast_request(
    channel,
    requester: discord.abc.User,
    target: discord.abc.User,
    request_text: str,
) -> str:
    if requester.id == target.id:
        return "Self-roast accepted: brave, chaotic, and one argument away from a group chat meltdown."

    recent, older = await collect_target_messages(channel, target.id)
    asyncio.create_task(refresh_profile_background(channel, target.id))
    profile = user_profiles.get(target.id, "No profile yet.")

    return await generate_dual_roast(
        request_text=request_text,
        target_name=target.display_name,
        requester_name=requester.display_name,
        recent=recent,
        older=older,
        profile=profile,
    )


# ───────────────────────────────────────
# Discord Events + Commands
# ───────────────────────────────────────
@bot.event
async def on_ready():
    candidate_list = ", ".join(MODEL_CANDIDATES)
    print(
        f"PsychBot online: {bot.user} | version={SCRIPT_VERSION} "
        f"| model_candidates=[{candidate_list}]"
    )


@bot.command(name="health")
async def health(ctx):
    model = active_model or "none-yet"
    err = last_groq_error if last_groq_error else "none"
    await ctx.send(
        f"version={SCRIPT_VERSION} model={model} groq_errors={groq_error_count} last_error={err}",
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.command(name="evaluate", aliases=["eval", "analyze", "analyse", "diagnose"])
async def evaluate_command(ctx, member: Optional[discord.Member] = None):
    target = member or ctx.author
    response = await handle_evaluate_like_request(ctx.channel, ctx.author, target, f"!{ctx.invoked_with}")
    await send_response(ctx.channel, target, response)
    channel_last_response_time[ctx.channel.id] = monotonic()


@bot.command(name="roast")
async def roast_command(ctx, member: Optional[discord.Member] = None):
    target = member or ctx.author
    response = await handle_roast_request(ctx.channel, ctx.author, target, f"!{ctx.invoked_with}")
    await send_response(ctx.channel, target, response)
    channel_last_response_time[ctx.channel.id] = monotonic()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # explicit prefix commands
    if (message.content or "").startswith("!"):
        await bot.process_commands(message)
        return

    if not remember_message_id(message.id):
        return

    original_text = (message.content or "")[:MAX_MESSAGE_CHARS]
    if not original_text.strip():
        await bot.process_commands(message)
        return

    text_lower = original_text.lower()
    now = monotonic()
    channel_id = message.channel.id
    last_response_time = channel_last_response_time.get(channel_id, 0.0)

    channel_memory = channel_memory_for(channel_id)
    channel_memory.append(("user", original_text))

    # automatic hate speech callout
    if likely_hate_speech(text_lower):
        callout = await generate_hate_speech_callout(original_text, message.author.display_name)
        await send_response(message.channel, message.author, callout)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # defensive mode
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

    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity. You analyzed it so hard you became the punchline.",
        )
        await bot.process_commands(message)
        return

    addressed = (bot.user in message.mentions) or ("psychbot" in text_lower) or is_reply_to_bot(message)

    # natural-language command handling
    if addressed and contains_any(text_lower, EVAL_TERMS):
        target = get_target_from_mentions(message)
        response = await handle_evaluate_like_request(message.channel, message.author, target, original_text)
        await send_response(message.channel, target, response)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    if addressed and contains_any(text_lower, ROAST_TERMS + KITCHEN_TERMS):
        target = get_target_from_mentions(message)
        response = await handle_roast_request(message.channel, message.author, target, original_text)
        await send_response(message.channel, target, response)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # normal open chat with bot
    if addressed:
        target = get_target_from_mentions(message)
        recent, older = await collect_target_messages(message.channel, target.id)
        profile = user_profiles.get(target.id, "No profile yet.")
        asyncio.create_task(refresh_profile_background(message.channel, target.id))

        reply = await generate_free_reply(
            message_text=original_text,
            memory=channel_memory,
            target_recent=recent,
            target_older=older,
            profile=profile,
            requester_name=message.author.display_name,
            roast_target_name=None,
            avoid_children=True,
        )
        await send_response(message.channel, target, reply)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # automatic distress / attack responses (lightweight and throttled)
    if now - last_response_time < cooldown_seconds:
        await bot.process_commands(message)
        return

    if is_emoji_only_distress(original_text):
        category = "NORMAL"
    elif likely_emotional_content(text_lower):
        category = await classify_message(original_text)
    else:
        category = "NORMAL"

    if category == "ATTACK":
        quick_roast = random.choice(
            [
                "That message was loud, not smart.",
                "Big confidence, tiny emotional regulation.",
                "You typed rage and called it personality.",
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
