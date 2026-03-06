import os
import discord
import asyncio
import random
from datetime import date
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# ---------------------------------------
# ENV VARIABLES
# ---------------------------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN:
    print("ERROR: DISCORD_TOKEN missing")
    exit(1)

if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY missing")
    exit(1)

# ---------------------------------------
# INIT GROQ
# ---------------------------------------
groq_client = Groq(api_key=GROQ_API_KEY)

# ---------------------------------------
# DISCORD BOT SETUP
# ---------------------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------
# STATE
# ---------------------------------------
chat_memory = deque(maxlen=60)
last_response_time = 0
cooldown = 8

user_streaks = {}
therapy_channels = set()

# ---------------------------------------
# CLASSIFY MESSAGE
# ---------------------------------------
def classify_message(text: str):

    prompt = f"""
Classify this Discord message.

ATTACK = insults, bullying, aggression
DISTRESS = sadness, frustration, emotional stress
NORMAL = anything else

Reply with ONE word only.

Message:
{text}
"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=5
        )

        result = response.choices[0].message.content.strip().upper()

        if "ATTACK" in result:
            return "ATTACK"

        if "DISTRESS" in result:
            return "DISTRESS"

        return "NORMAL"

    except Exception as e:
        print("Classification error:", e)
        return "NORMAL"


# ---------------------------------------
# ROAST BULLY
# ---------------------------------------
def generate_attack_response(username: str, message: str):

    prompt = f"""
User "{username}" is talking trash in Discord.

Call them out.

Rules:
- witty
- blunt
- short
- slightly roasting but not cruel
- 1–2 sentences
- directly reference their username

Message:
{message}
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=80
    )

    return response.choices[0].message.content.strip()


# ---------------------------------------
# DISTRESS RESPONSE
# ---------------------------------------
def generate_distress_response(username: str, message: str):

    prompt = f"""
Someone sounds stressed.

Respond briefly.

Tone:
- human
- supportive
- slightly witty
- 1–2 sentences
- address them by username

User: {username}

Message:
{message}
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=90
    )

    return response.choices[0].message.content.strip()


# ---------------------------------------
# DIRECT CHAT
# ---------------------------------------
def generate_direct_chat_response(msg: str):

    prompt = f"""
Respond to this Discord user.

Rules:
- short
- human
- helpful
- max 2 sentences

User message:
{msg}
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.65,
        max_tokens=120
    )

    return response.choices[0].message.content.strip()


# ---------------------------------------
# USER PSYCHOLOGICAL EVALUATION
# ---------------------------------------
def generate_user_eval(target_name, recent_msgs, older_msgs, mode):

    recent = "\n".join(recent_msgs) if recent_msgs else "(none)"
    older = "\n".join(older_msgs) if older_msgs else "(none)"

    prompt = f"""
Psychological observation of Discord user.

Rules:
- 2 sentences
- observational
- not speculative
- calm tone
- no emojis

Person: {target_name}
Focus: {mode}

Recent messages:
{recent}

Older messages:
{older}
"""

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=120
    )

    return response.choices[0].message.content.strip()


# ---------------------------------------
# BOT READY
# ---------------------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


# ---------------------------------------
# MESSAGE HANDLER
# ---------------------------------------
@bot.event
async def on_message(message):

    global last_response_time

    if message.author.bot:
        return

    text = message.content.strip()
    username = message.author.display_name
    user_id = str(message.author.id)
    today = date.today()

    chat_memory.append(f"{username}: {text}")

    # -----------------------------
    # Mood tracking
    # -----------------------------
    negative_words = ["hate","stupid","ugly","worthless","kys"]

    is_negative = any(w in text.lower() for w in negative_words)

    if user_id not in user_streaks:
        user_streaks[user_id] = {
            "good_days":0,
            "bad_days":0,
            "last_date":today
        }

    streak = user_streaks[user_id]

    if streak["last_date"] < today:

        if is_negative:
            streak["bad_days"] += 1
            streak["good_days"] = 0
        else:
            streak["good_days"] += 1
            streak["bad_days"] = 0

        streak["last_date"] = today

    # -----------------------------
    # Cooldown
    # -----------------------------
    now = asyncio.get_event_loop().time()

    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    # -----------------------------
    # Classification
    # -----------------------------
    category = classify_message(text)

    try:

        # ---------------------------------
        # ATTACK / BULLY
        # ---------------------------------
        if category == "ATTACK":

            roast = generate_attack_response(username, text)

            await message.reply(roast)

            last_response_time = now


        # ---------------------------------
        # DISTRESS
        # ---------------------------------
        elif category == "DISTRESS":

            reply = generate_distress_response(username, text)

            await message.reply(reply)

            last_response_time = now


        # ---------------------------------
        # BOT MENTIONED -> EVALUATION
        # ---------------------------------
        elif bot.user in message.mentions:

            targets = [m for m in message.mentions if m != bot.user]

            if targets:

                target = targets[0]

                mode = "overall"

                if "past" in text.lower():
                    mode = "past"

                elif "recent" in text.lower():
                    mode = "recent"

                recent_msgs = []
                older_msgs = []

                async for msg in message.channel.history(limit=400):

                    if msg.author == target and msg.content:

                        if len(recent_msgs) < 8:
                            recent_msgs.append(msg.content)

                        elif len(older_msgs) < 6:
                            older_msgs.append(msg.content)

                    if len(recent_msgs) + len(older_msgs) >= 14:
                        break

                evaluation = generate_user_eval(
                    target.display_name,
                    recent_msgs,
                    older_msgs,
                    mode
                )

                await message.reply(
                    f"Psych eval for {target.mention}:\n{evaluation}"
                )

                last_response_time = now

            else:

                reply = generate_direct_chat_response(text)

                await message.reply(reply)

                last_response_time = now

    except Exception as e:
        print("AI response error:", e)

    await bot.process_commands(message)


# ---------------------------------------
# COMMANDS
# ---------------------------------------
@bot.command()
async def therapy(ctx, state=None):

    if state == "on":
        therapy_channels.add(ctx.channel.id)
        await ctx.send("🧠 Therapy mode enabled.")

    elif state == "off":
        therapy_channels.discard(ctx.channel.id)
        await ctx.send("🧠 Therapy mode disabled.")

    else:
        await ctx.send("Usage: !therapy on/off")


@bot.command()
async def selfdestruct(ctx):

    prompt = f"""
Roast {ctx.author.display_name} lightly.

Rules:
- witty
- harmless
- max 2 sentences
"""

    try:

        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0.9,
            max_tokens=80
        )

        await ctx.reply(response.choices[0].message.content.strip())

    except:
        await ctx.reply("Couldn't roast you this time.")


# ---------------------------------------
# RUN
# ---------------------------------------
bot.run(DISCORD_TOKEN)
