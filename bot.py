import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

# -------------------------
# ENV
# -------------------------
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not DISCORD_TOKEN or not GROQ_API_KEY:
    print("Missing env variables")
    exit()

groq_client = Groq(api_key=GROQ_API_KEY)

# -------------------------
# BOT SETUP
# -------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# STATE
# -------------------------
chat_memory = deque(maxlen=50)
cooldown = 8
last_response_time = 0


# -------------------------
# MESSAGE CLASSIFIER
# -------------------------
def classify_message(text):

    prompt = f"""
Classify this Discord message.

ATTACK = bullying, insults
DISTRESS = emotional frustration
NORMAL = everything else

Return ONE word.

Message:
{text}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
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


# -------------------------
# ROAST GENERATOR
# -------------------------
def generate_roast(name, message):

    prompt=f"""
User {name} was talking trash.

Call them out.

Rules:
- witty
- blunt
- 1-2 sentences
- short

Message:
{message}
"""

    r=groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=80
    )

    return r.choices[0].message.content.strip()


# -------------------------
# DISTRESS SUPPORT
# -------------------------
def generate_support(name,msg):

    prompt=f"""
User {name} sounds stressed.

Reply briefly.

Rules
- supportive
- casual
- 1 sentence

Message:
{msg}
"""

    r=groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=60
    )

    return r.choices[0].message.content.strip()


# -------------------------
# PSYCHOLOGICAL EVAL
# -------------------------
def generate_eval(name,recent,older):

    recent_text="\n".join(recent)
    older_text="\n".join(older)

    prompt=f"""
Provide a short psychological observation.

Rules:
- 2 sentences
- observational
- no speculation

User: {name}

Recent messages:
{recent_text}

Older messages:
{older_text}
"""

    r=groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7,
        max_tokens=120
    )

    return r.choices[0].message.content.strip()


# -------------------------
# READY
# -------------------------
@bot.event
async def on_ready():
    print(f"Bot ready: {bot.user}")


# -------------------------
# MAIN MESSAGE HANDLER
# -------------------------
@bot.event
async def on_message(message):

    global last_response_time

    if message.author.bot:
        return

    responded = False
    text = message.content.strip()

    chat_memory.append(f"{message.author.display_name}: {text}")

    now = asyncio.get_event_loop().time()

    if now - last_response_time < cooldown:
        await bot.process_commands(message)
        return

    # -------------------------
    # BOT MENTION COMMANDS
    # -------------------------
    if bot.user in message.mentions and not responded:

        targets = [m for m in message.mentions if m != bot.user]

        if targets:

            target = targets[0]

            # ROAST REQUEST
            if "roast" in text.lower():

                roast = generate_roast(target.display_name,text)

                await message.reply(
                    f"{target.mention} {roast}"
                )

                responded=True
                last_response_time=now


            # EVALUATE REQUEST
            elif "eval" in text.lower() or "evaluate" in text.lower():

                recent=[]
                older=[]

                async for msg in message.channel.history(limit=400):

                    if msg.author.id==target.id:

                        if len(recent)<8:
                            recent.append(msg.content)

                        elif len(older)<6:
                            older.append(msg.content)

                    if len(recent)+len(older)>=14:
                        break

                result=generate_eval(target.display_name,recent,older)

                await message.reply(
                    f"Psych eval for {target.mention}:\n{result}"
                )

                responded=True
                last_response_time=now

    # -------------------------
    # AUTO MODERATION
    # -------------------------
    if not responded:

        category = classify_message(text)

        if category=="ATTACK":

            roast = generate_roast(message.author.display_name,text)

            await message.reply(
                f"{message.author.mention} {roast}"
            )

            responded=True
            last_response_time=now

        elif category=="DISTRESS":

            support=generate_support(message.author.display_name,text)

            await message.reply(
                f"{message.author.mention} {support}"
            )

            responded=True
            last_response_time=now

    await bot.process_commands(message)


# -------------------------
# SELF ROAST
# -------------------------
@bot.command()
async def selfdestruct(ctx):

    prompt=f"""
Roast {ctx.author.display_name}.

Rules
- funny
- harmless
- one sentence
"""

    r=groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=60
    )

    await ctx.reply(r.choices[0].message.content.strip())


# -------------------------
# RUN
# -------------------------
bot.run(DISCORD_TOKEN)
