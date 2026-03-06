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
    print("ERROR: DISCORD_TOKEN missing from .env")
    exit(1)
if not GROQ_API_KEY:
    print("ERROR: GROQ_API_KEY missing from .env")
    exit(1)

# ---------------------------------------
# INIT GROQ
# ---------------------------------------
try:
    groq_client = Groq(api_key=GROQ_API_KEY)
except Exception as e:
    print("Groq client init failed:", e)
    exit(1)

# ---------------------------------------
# DISCORD BOT SETUP
# ---------------------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ---------------------------------------
# STATE
# ---------------------------------------
chat_memory = deque(maxlen=50)
last_response_time = 0
cooldown = 10
therapy_channels = set()
user_streaks = {}  # user_id -> {'good_days': int, 'bad_days': int, 'last_date': date}

# ---------------------------------------
# CLASSIFY MESSAGE
# ---------------------------------------
def classify_message(text: str):
    prompt = f"""
You are a chat analyzer.

Classify this Discord message into one category:

ATTACK -> insult, aggression, mockery
DISTRESS -> sadness, stress, frustration
NORMAL -> everything else

Return only the category word (ATTACK, DISTRESS, NORMAL).

Message:
{text}
"""
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0
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
# AI RESPONSE FUNCTIONS
# ---------------------------------------
def generate_attack_response(context: str):
    prompt = f"""
Someone attacked another user in Discord.

Respond in a way that:
- blunt and to the point
- tells them how it is
- insightful, slightly witty
- 2-3 sentences max
- not cruel, not insulting

Conversation context:
{context}
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.85,
        max_tokens=120
    )
    return response.choices[0].message.content.strip()

def generate_distress_response(context: str):
    prompt = f"""
Someone in the chat seems distressed.

Respond in a way that:
- checks on them
- slightly witty and funny
- human sounding
- 2-3 sentences max
- supportive without preaching

Conversation context:
{context}
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.8,
        max_tokens=160
    )
    return response.choices[0].message.content.strip()

def generate_direct_chat_response(user_msg: str):
    prompt = f"""
You are a helpful psychological AI.

Respond directly to this user message.
3-4 sentences max.
User message: "{user_msg}"
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.65,
        max_tokens=160
    )
    return response.choices[0].message.content.strip()

def generate_user_eval(target_name: str, recent_msgs: list, older_msgs: list, mode: str):
    recent_str = "\n".join(recent_msgs) if recent_msgs else "(no recent)"
    older_str = "\n".join(older_msgs) if older_msgs else "(no older)"
    prompt = f"""
Evidence-based psychological evaluation. Note tone: calmer, aggressive, etc.
Mostly positive/neutral -> say so clearly.
2-3 sentences max, no speculation, no emojis.

Person: {target_name}
Focus: {mode}
Recent:
{recent_str}
Older:
{older_str}
"""
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=140
    )
    return response.choices[0].message.content.strip()

# ---------------------------------------
# BOT EVENTS
# ---------------------------------------
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} - Psycho mode activated!')

@bot.event
async def on_message(message):
    global last_response_time
    if message.author.bot:
        return

    text = message.content.strip()
    user_id = str(message.author.id)
    today = date.today()
    chat_memory.append(f"{message.author.display_name}: {text}")
    context = "\n".join(chat_memory)

    # -----------------------------
    # Mood streak tracking
    # -----------------------------
    is_negative = any(word in text.lower() for word in ['hate','stupid','kill yourself','ugly','worthless'])
    if user_id not in user_streaks:
        user_streaks[user_id] = {'good_days':0,'bad_days':0,'last_date':today}
    streak = user_streaks[user_id]
    if streak['last_date'] < today:
        if is_negative:
            streak['bad_days'] += 1
            streak['good_days'] = 0
        else:
            streak['good_days'] += 1
            streak['bad_days'] = 0
        streak['last_date'] = today
        if streak['good_days'] >= 4 and random.random() < 0.1:
            await message.channel.send(f"Day {streak['good_days']} of calm vibes. {message.author.mention}, you're surprisingly chill.")

    # -----------------------------
    # Distress keyword check
    # -----------------------------
    distress_keywords = ['rope','unalive','end it','kms','kys','jump off','pill bottle']
    if any(kw in text.lower() for kw in distress_keywords):
        replies = [
            "Third 'rope' joke this week. Stand-up material or the void calling? You good fr?",
            "Dark humor level: concerning. What's actually weighing on you?"
        ]
        await message.reply(random.choice(replies))
        return

    # -----------------------------
    # Message classification
    # -----------------------------
    category = classify_message(text)
    now = asyncio.get_event_loop().time()
    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    try:
        # Direct attack
        if category == "ATTACK":
            response = generate_attack_response(context)
            await message.channel.send(response)
            last_response_time = now
        # Distress
        elif category == "DISTRESS":
            response = generate_distress_response(context)
            await message.channel.send(response)
            last_response_time = now
        # Bot mentioned -> evaluation
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
                async for msg in message.channel.history(limit=500):
                    if msg.author == target and msg.content.strip() and not msg.content.startswith('Psych eval'):
                        if len(recent_msgs) < 8:
                            recent_msgs.append(msg.content)
                        elif len(older_msgs) < 6:
                            older_msgs.append(msg.content)
                    if len(recent_msgs) + len(older_msgs) >= 14:
                        break
                reply_text = generate_user_eval(target.display_name, recent_msgs, older_msgs, mode)
                await message.reply(f"Psych eval for {target.mention} ({mode}):\n{reply_text}")
                last_response_time = now
            else:
                reply_text = generate_direct_chat_response(text)
                await message.reply(reply_text)
                last_response_time = now
    except Exception as e:
        print("AI response error:", e)

    await bot.process_commands(message)

# ---------------------------------------
# COMMANDS
# ---------------------------------------
@bot.command(name='therapy')
async def toggle_therapy(ctx, state: str = None):
    if state == 'on':
        therapy_channels.add(ctx.channel.id)
        await ctx.send("🧠 Therapy mode enabled for this channel.")
    elif state == 'off':
        therapy_channels.discard(ctx.channel.id)
        await ctx.send("🧠 Therapy mode disabled.")
    else:
        await ctx.send("Usage: `!therapy on` or `!therapy off`")

@bot.command(name='selfdestruct')
async def selfdestruct(ctx):
    if not groq_client:
        await ctx.reply("AI unavailable.")
        return
    prompt = f"Write a witty but harmless self-roast for {ctx.author.display_name}. Max 2 sentences."
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0.8,
            max_tokens=100
        )
        await ctx.reply(response.choices[0].message.content.strip())
    except Exception as e:
        print("Self roast error:", e)
        await ctx.reply("Couldn't roast you this time.")

# ---------------------------------------
# RUN BOT
# ---------------------------------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
