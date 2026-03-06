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
bot_memory = {}  # channel_id -> deque of (role, content)
user_profiles = {}  # user_id -> profile
PROGRAMMER_NAME = "Austin"


# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    return text.replace("@", "").replace("<", "").replace(">", "").strip()


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
    async for msg in channel.history(limit=200):
        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)
        if len(history) >= 40:
            break
    profile = generate_user_profile(history)
    if profile:
        user_profiles[user_id] = profile


# ───────────── AI Response Generators ─────────────
async def generate_roast_or_analysis(message_text, channel_memory, target_user=None,
                                     requester_name=None):
    roast_target_name = target_user.display_name if target_user else None
    profile = "No profile yet."
    if target_user:
        await update_user_profile(message.channel, target_user.id)
        profile = user_profiles.get(target_user.id, profile)

    memory_text = "\n".join(
        [f"{role}: {content}" for role, content in list(channel_memory)[-5:]]
    )
    prompt_parts = []

    if roast_target_name and requester_name:
        prompt_parts.append(
            f"First roast {roast_target_name} directly, then roast {requester_name} "
            "for asking a bot to roast. Keep it short, witty, safe, no user IDs, "
            "no playgrounds, no unsafe age jokes."
        )

    prompt = f"""
You are PsychBot: witty, sarcastic, psychologically observant, dark humor.
Rules: respond as the bot, never speak for others, avoid therapy language.
Programmer {PROGRAMMER_NAME} is normal.
Conversation history: {memory_text}
User profile: {profile}
User message: {message_text}
Write one short paragraph response.
{''.join(prompt_parts)}
"""

    for attempt in range(3):
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
                max_tokens=120
            )
            result = clean_ai_output(r.choices[0].message.content)
            if result:
                return result
        except:
            continue

    fallback_roasts = [
        f"Oops, I tried roasting {roast_target_name or 'someone'} but circuits fried.",
        "AI malfunction prevented a roast. You're lucky this time.",
        "Pretend I nailed the roast. Really, I did."
    ]
    return random.choice(fallback_roasts)


def generate_support(text):
    prompt = f"""
Someone posted this:
{text}
Reply with one supportive but slightly humorous sentence.
Keep it short, do NOT sound like a therapist.
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
        return "Rough day? At least Discord is cheaper than therapy."


def generate_fact_answer(message_text):
    prompt = f"""
Answer concisely. Then add: 'But I'm not here to diagnose anyone.'
Question:
{message_text}
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=80
        )
        return clean_ai_output(r.choices[0].message.content)
    except:
        return "I don't know, but I'm not here to diagnose anyone."


async def generate_short_free_reply(message_text, channel_memory):
    memory_text = "\n".join(
        [f"{role}: {content}" for role, content in list(channel_memory)[-5:]]
    )
    prompt = f"""
You are PsychBot: witty, sarcastic, psychologically observant, dark humor.
Rules: respond as the bot, short and witty, avoid therapy language.
Conversation history: {memory_text}
User message: {message_text}
Write a one-sentence reply.
"""
    for attempt in range(3):
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=50
            )
            result = clean_ai_output(r.choices[0].message.content)
            if result:
                return result
        except:
            continue
    return "My circuits just hiccuped. Pretend I replied."


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
    if message.author.bot or message.id in processed_messages:
        return
    processed_messages.append(message.id)

    original_text = message.content[:500]
    text_lower = original_text.lower()
    channel_id = message.channel.id
    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=20)
    bot_memory[channel_id].append(("user", original_text))
    channel_memory = bot_memory[channel_id]

    # Criticism triggers
    criticism_triggers = ["ai slop", "ai garbage", "bad bot", "dumb bot", "stupid bot"]
    if any(trigger in text_lower for trigger in criticism_triggers):
        responses = [
            "Me? Slop? Bold claim from a human.",
            "Careful, your opinion just got roasted instead.",
            "Wow, coming for a bot? Courageous.",
            "And here I thought humans were funny."
        ]
        await send_response(message.channel, message.author, random.choice(responses))
        return

    # Humor defense
    if "how is" in text_lower and ("humor" in text_lower or "humour" in text_lower):
        await send_response(
            message.channel,
            message.author,
            "Humor is confidence plus absurdity — analyzing it just added another punchline."
        )
        return

    # Roast / analyze / profile / evaluate triggers
    special_keywords = [
        "roast", "make fun of", "mock", "insult", "clown on", "destroy him",
        "evaluate", "diagnose", "analyze", "profile"
    ]
    if any(k in text_lower for k in special_keywords):
        targets = [m for m in message.mentions if m.id != bot.user.id]
        target_user = targets[0] if targets else None
        requester_name = message.author.display_name
        reply = await generate_roast_or_analysis(
            original_text, channel_memory, target_user=target_user,
            requester_name=requester_name
        )
        await send_response(message.channel, message.author, reply)
        return

    # Fact questions
    fact_keywords = ["who", "what", "where", "when", "why", "how", "is", "are"]
    if any(original_text.lower().startswith(k) for k in fact_keywords):
        reply = generate_fact_answer(original_text)
        await send_response(message.channel, message.author, reply)
        return

    # Short normal conversation
    mentioned = bot.user in message.mentions or any(w in text_lower for w in ["psychbot", "bot"])
    if mentioned:
        reply = await generate_short_free_reply(original_text, channel_memory)
        await send_response(message.channel, message.author, reply)
        return

    # Distress
    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in original_text)
    category = classify_message(original_text)
    if emoji_only:
        category = "NORMAL"
    if category == "DISTRESS":
        support = generate_support(original_text)
        await send_response(message.channel, message.author, support)

    await bot.process_commands(message)


# ───────────── Run bot ─────────────
bot.run(DISCORD_TOKEN)
