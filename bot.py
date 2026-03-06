import os
import discord
import asyncio
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
    print("Missing DISCORD_TOKEN or GROQ_API_KEY")
    exit(1)

groq_client = Groq(api_key=GROQ_API_KEY)

# ───────────────────────────────────────
# Bot setup
# ───────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals
# ───────────────────────────────────────
processing_lock = asyncio.Lock()
processed_messages = set()
cooldown = 8
last_response_time = 0

# ───────────────────────────────────────
# Helpers
# ───────────────────────────────────────
def clean_ai_output(text):
    text = text.replace("@", "")
    text = text.replace("<", "").replace(">", "")
    return text.strip()

def classify_message(text):
    prompt = f"""
You are the strictest Discord message classifier possible. Do NOT overreact.

Return ONLY one word:

ATTACK = ONLY severe, direct, targeted bullying with clear intent to harm one specific person, such as:
  - Telling one person to kill themselves / self-harm
  - Repeated cruel mockery of disability, trauma, race, sexuality, appearance, or mental health
  - Personal attacks designed to deeply degrade one individual

DISTRESS = obvious emotional distress, suicidal hints, despair, or cry for help

NORMAL = EVERYTHING ELSE, including:
  - Group banter ("good morning fucktards", "morning losers", "fuck you all", "suck my dick everyone")
  - Casual trash talk, swearing, one-off insults ("go fuck yourself", "you're trash", "dumbass")
  - Playful teasing, sarcasm, edgy humor, jokes, memes
  - Compliments, flirting, light-hearted rudeness
  - Anything ambiguous, funny, group-directed, or not clearly meant to bully one person

If it's not 100% unmistakable personal bullying, ALWAYS return NORMAL. Casual swearing and banter are NORMAL.

Message:
{text}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # zero temperature = strict, no creativity, no false positives
            max_tokens=5
        )
        result = r.choices[0].message.content.upper().strip()
        if "ATTACK" in result:
            return "ATTACK"
        if "DISTRESS" in result:
            return "DISTRESS"
        return "NORMAL"
    except Exception as e:
        print(f"Classification error: {e}")
        return "NORMAL"

def generate_roast(target_recent, target_older):
    recent = "\n".join(target_recent[-12:]) if target_recent else "No recent messages."
    older = "\n".join(target_older[:8]) if target_older else ""

    prompt = f"""
You are a savage, witty, psychologically sharp roaster with a dark sense of humor.

Analyze the target's message history. Find their unique quirks, contradictions, ego traps, hidden fears, or funny blind spots.
Deliver ONE original, hilarious-yet-cutting sentence that roasts their psychological core in an entertaining way.

Rules:
- Make it funny / clever / entertaining (dark humor OK)
- Be personal and insightful — use real patterns from messages
- NO generic tropes ("attention seeking", "mask", "crying for help", "defense mechanism")
- NO shallow burns (hair, clothes, looks, skills, "sexy")
- NO names
- One sentence only
- Always fresh, varied, and surprising — never repeat yourself

Recent messages:
{recent}

Older messages (if any):
{older}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.95,  # higher for creativity & humor variety
            max_tokens=90
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Roast error: {e}")
        return None

def generate_support():
    prompt = """
Someone seems stressed or in distress.

Respond with one kind, grounding, slightly humorous sentence.
Do not include names.
Be calm and sincere, with a light touch of wit.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=60
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Support error: {e}")
        return "Take a breath, champ — the universe isn't done fucking with you yet, but you're tougher than it thinks."

def generate_eval(recent, older):
    r_text = "\n".join(recent[-8:]) if recent else ""
    o_text = "\n".join(older[:6]) if older else ""

    if len(recent) + len(older) < 3:
        fallback_prompt = """
The target has very little message history.

Give one neutral, observational sentence about someone who rarely speaks in group settings.
Keep it psychological, not insulting.
No names.
"""
        try:
            r = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": fallback_prompt}],
                temperature=0.6,
                max_tokens=60
            )
            return clean_ai_output(r.choices[0].message.content)
        except:
            return "Limited message history available — the target appears reserved or selectively engaged."

    prompt = f"""
Create a short, evidence-based psychological observation.

Rules:
- Exactly 2 sentences
- Neutral, clinical tone
- No names
- Base it only on the messages provided
- Focus on patterns, tone shifts, coping styles, emotional themes

Recent:
{r_text}

Older:
{o_text}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=120
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Eval error: {e}")
        return "Insufficient message history for meaningful observation."

async def send_response(channel, target, text):
    if text is None:
        return
    try:
        await channel.send(f"{target.mention} {text}")
    except Exception as e:
        print(f"Send error: {e}")

# ───────────────────────────────────────
# Events
# ───────────────────────────────────────
@bot.event
async def on_ready():
    print(f"PsychBot online: {bot.user}")

@bot.event
async def on_message(message):
    global last_response_time

    if message.author.bot:
        return

    async with processing_lock:
        if message.id in processed_messages:
            return
        processed_messages.add(message.id)

        text = message.content.lower()
        now = asyncio.get_event_loop().time()

        # Bot mentioned → special commands first
        if bot.user in message.mentions:
            targets = [m for m in message.mentions if m.id != bot.user.id]
            if not targets:
                return
            target = targets[0]

            content_lower = message.content.lower()

            eval_keywords = ["diagnose", "evaluate", "eval", "psych", "analysis", "assess", "psychological"]
            if any(kw in content_lower for kw in eval_keywords):
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

                result = generate_eval(recent, older)
                await send_response(message.channel, target, result)
                last_response_time = now
                return

            if "roast" in content_lower:
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

                roast = generate_roast(recent, older)
                await send_response(message.channel, target, roast)
                last_response_time = now
                return

        # Auto-detection – ONLY blatant ATTACK
        if now - last_response_time < cooldown:
            await bot.process_commands(message)
            return

        category = classify_message(text)

        if category == "ATTACK":
            recent = []
            older = []
            async for msg in message.channel.history(limit=400):
                if msg.author.id == message.author.id and msg.content.strip():
                    if len(recent) < 8:
                        recent.append(msg.content)
                    elif len(older) < 6:
                        older.append(msg.content)
                    if len(recent) + len(older) >= 14:
                        break

            roast = generate_roast(recent, older)
            if roast:
                await send_response(message.channel, message.author, roast)
            last_response_time = now

        elif category == "DISTRESS":
            support = generate_support()
            if support:
                await send_response(message.channel, message.author, support)
            last_response_time = now

        await bot.process_commands(message)

# ───────────────────────────────────────
# Run
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
