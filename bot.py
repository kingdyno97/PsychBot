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


def safe_groq_sync(prompt, max_tokens=100, temperature=0.9, retries=3):
    for _ in range(retries):
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = r.choices[0].message.content.strip()
            if content:
                return content
        except:
            continue
    return None


async def safe_groq_async(prompt, max_tokens=100, temperature=0.9, retries=3):
    for _ in range(retries):
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens
            )
            content = r.choices[0].message.content.strip()
            if content:
                return content
        except:
            await asyncio.sleep(0.2)
            continue
    return None


async def update_user_profile(channel, user_id):
    history = []
    async for msg in channel.history(limit=50):
        if msg.author.id == user_id and msg.content.strip():
            history.append(msg.content)
        if len(history) >= 20:
            break
    prompt = f"Analyze user messages:\n{history}\nWrite a 2 sentence observation."
    result = safe_groq_sync(prompt, max_tokens=60)
    if result:
        user_profiles[user_id] = result


async def generate_roast_or_analysis(message_text, channel, channel_memory, target_user=None,
                                     requester_name=None):
    roast_target_name = target_user.display_name if target_user else None
    profile = "No profile yet."
    if target_user:
        await update_user_profile(channel, target_user.id)
        profile = user_profiles.get(target_user.id, profile)

    memory_text = "\n".join([f"{role}: {content}" for role, content in list(channel_memory)[-5:]])

    prompt_parts = ""
    if roast_target_name and requester_name:
        prompt_parts = (
            f"Roast {roast_target_name} and then {requester_name} for asking a bot. "
            "Keep it short, safe, no user IDs, no playgrounds."
        )

    prompt = f"""
You are PsychBot: witty, sarcastic, dark humor.
Conversation history: {memory_text}
User profile: {profile}
Message: {message_text}
{prompt_parts}
Write a short paragraph.
"""
    result = await safe_groq_async(prompt, max_tokens=100)
    if not result:
        fallback = [
            f"Oops, I tried roasting {roast_target_name or 'someone'} but circuits fried.",
            "Pretend I nailed the roast. Really, I did."
        ]
        return random.choice(fallback)
    return clean_ai_output(result)


def generate_support(text):
    prompt = f"Someone posted:\n{text}\nReply with a short supportive sentence."
    result = safe_groq_sync(prompt, max_tokens=40)
    return clean_ai_output(result) if result else "Rough day? At least Discord is cheaper than therapy."


def generate_fact_answer(text):
    prompt = f"Answer briefly:\n{text}\nThen say: 'But I'm not here to diagnose anyone.'"
    result = safe_groq_sync(prompt, max_tokens=80, temperature=0)
    return clean_ai_output(result) if result else "I don't know, but I'm not here to diagnose anyone."


async def generate_short_free_reply(message_text, channel_memory):
    memory_text = "\n".join([f"{role}: {content}" for role, content in list(channel_memory)[-5:]])
    prompt = f"PsychBot short witty reply:\nConversation: {memory_text}\nMessage: {message_text}"
    result = await safe_groq_async(prompt, max_tokens=50)
    return clean_ai_output(result) if result else "I hiccuped, but I hear you."


async def send_response(channel, target, text):
    if not text:
        text = "My circuits fried."
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

    text_lower = message.content.lower()
    channel_id = message.channel.id
    if channel_id not in bot_memory:
        bot_memory[channel_id] = deque(maxlen=20)
    bot_memory[channel_id].append(("user", message.content))
    channel_memory = bot_memory[channel_id]

    # Criticism triggers
    if any(word in text_lower for word in ["ai slop", "bad bot", "dumb bot"]):
        await send_response(message.channel, message.author, "Bold opinion. Here's a roast for free.")
        return

    # Roast / analyze / evaluate triggers
    if any(word in text_lower for word in ["roast", "mock", "analyze", "profile", "evaluate"]):
        targets = [m for m in message.mentions if m.id != bot.user.id]
        target_user = targets[0] if targets else None
        reply = await generate_roast_or_analysis(
            message.content, message.channel, channel_memory, target_user, message.author.display_name
        )
        await send_response(message.channel, message.author, reply)
        return

    # Fact questions
    if any(message.content.lower().startswith(k) for k in ["who", "what", "where", "when", "why", "how", "is", "are"]):
        reply = generate_fact_answer(message.content)
        await send_response(message.channel, message.author, reply)
        return

    # Short free-form reply
    if bot.user in message.mentions:
        reply = await generate_short_free_reply(message.content, channel_memory)
        await send_response(message.channel, message.author, reply)
        return

    # Distress
    emoji_only = all(c in "😭😢🥺😞😔😿 " for c in message.content)
    if classify_message(message.content) == "DISTRESS" and not emoji_only:
        reply = generate_support(message.content)
        await send_response(message.channel, message.author, reply)

    await bot.process_commands(message)


# ───────────── Run bot ─────────────
bot.run(DISCORD_TOKEN)
