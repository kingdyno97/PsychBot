import os
import discord
import asyncio
from collections import deque
from discord.ext import commands
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

chat_memory = deque(maxlen=50)

cooldown = 8
last_response_time = 0

# prevents duplicate event triggers
message_cache = set()


# -------------------------
# CLASSIFIER
# -------------------------
def classify_message(text):

    prompt=f"""
Classify this message.

ATTACK = bullying or insults
DISTRESS = emotional stress
NORMAL = everything else

Return one word.

Message:
{text}
"""

    try:
        r=groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"user","content":prompt}],
            temperature=0,
            max_tokens=5
        )

        result=r.choices[0].message.content.upper()

        if "ATTACK" in result:
            return "ATTACK"

        if "DISTRESS" in result:
            return "DISTRESS"

        return "NORMAL"

    except:
        return "NORMAL"


# -------------------------
# ROAST
# -------------------------
def generate_roast(name,msg):

    prompt=f"""
User {name} is talking trash.

Call them out.

Rules:
- witty
- blunt
- one sentence
"""

    r=groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role":"user","content":prompt}],
        temperature=0.9,
        max_tokens=60
    )

    return r.choices[0].message.content.strip()


# -------------------------
# SUPPORT
# -------------------------
def generate_support(name,msg):

    prompt=f"""
User {name} seems stressed.

Reply with one supportive sentence.

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
# EVALUATION
# -------------------------
def generate_eval(name,recent,older):

    r_text="\n".join(recent)
    o_text="\n".join(older)

    prompt=f"""
Short psychological observation.

Rules:
- 2 sentences
- observational
- no speculation

User: {name}

Recent:
{r_text}

Older:
{o_text}
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
    print(f"PsychBot online as {bot.user}")


# -------------------------
# MESSAGE HANDLER
# -------------------------
@bot.event
async def on_message(message):

    global last_response_time

    if message.author.bot:
        return

    # prevent duplicate processing
    if message.id in message_cache:
        return

    message_cache.add(message.id)

    text = message.content.strip()

    now = asyncio.get_event_loop().time()

    # -------------------------
    # BOT COMMAND VIA MENTION
    # -------------------------
    if bot.user in message.mentions:

        targets=[m for m in message.mentions if m!=bot.user]

        if targets:

            target=targets[0]

            # ROAST COMMAND
            if "roast" in text.lower():

                roast=generate_roast(target.display_name,text)

                await message.reply(
                    f"{target.mention} {roast}",
                    mention_author=False
                )

                return


            # EVALUATE COMMAND
            if "eval" in text.lower() or "evaluate" in text.lower():

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
                    f"Psych eval for {target.mention}:\n{result}",
                    mention_author=False
                )

                return


    # -------------------------
    # COOLDOWN
    # -------------------------
    if now-last_response_time < cooldown:
        return

    # -------------------------
    # AUTO MODERATION
    # -------------------------
    category=classify_message(text)

    if category=="ATTACK":

        roast=generate_roast(message.author.display_name,text)

        await message.reply(
            f"{message.author.mention} {roast}",
            mention_author=False
        )

        last_response_time=now
        return


    if category=="DISTRESS":

        support=generate_support(message.author.display_name,text)

        await message.reply(
            f"{message.author.mention} {support}",
            mention_author=False
        )

        last_response_time=now
        return


# -------------------------
# SELF ROAST
# -------------------------
@bot.command()
async def selfdestruct(ctx):

    prompt=f"Roast {ctx.author.display_name} in one funny sentence."

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
