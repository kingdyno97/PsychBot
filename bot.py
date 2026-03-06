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
    print("Missing DISCORD_TOKEN or GROQ_API_KEY in environment variables")
    exit(1)

groq_client = Groq(api_key=GROQ_API_KEY)

# ───────────────────────────────────────
# Bot setup
# ───────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ───────────────────────────────────────
# Globals (only what's actually used)
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
Classify this Discord message as one word only:

ATTACK = bullying / insult / aggression
DISTRESS = emotional distress / suicidal hints / despair
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
    recent = "\n".join(target_recent[-8:]) if target_recent else "No recent messages."
    older = "\n".join(target_older[:6]) if target_older else ""

    prompt = f"""
You are a brutally honest, psychologically incisive roaster.

Analyze the target's message history below.
Identify their core insecurities, contradictions, defense mechanisms, or repeating emotional patterns.
Deliver ONE devastating, deeply personal sentence that cuts straight to that psychological wound.

Rules:
- Be surgical and insightful — go for the emotional/ego core
- Do NOT make shallow roasts (hair, clothes, looks, skills)
- Do NOT mention names
- One sentence only

Recent messages:
{recent}

Older messages (if any):
{older}
"""

    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.85,
            max_tokens=80
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Roast error: {e}")
        return "Your whole persona is just a mask — and it's cracking."

def generate_support():
    prompt = """
Someone seems stressed or in distress.

Respond with one kind, grounding sentence.
Do not include names.
Be calm and sincere.
"""
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=60
        )
        return clean_ai_output(r.choices[0].message.content)
    except Exception as e:
        print(f"Support error: {e}")
        return "You're allowed to feel this way — breathe, you're not alone."

def generate_eval(recent, older):
    r_text = "\n".join(recent[-8:]) if recent else ""
    o_text = "\n".join(older[:6]) if older else ""

    if len(recent) + len(older) < 3:
        fallback_prompt = """
The target has very little message history in this channel.

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

        # Bot is mentioned → handle special commands first
        if bot.user in message.mentions:
            targets = [m for m in message.mentions if m.id != bot.user.id]
            if not targets:
                return
            target = targets[0]

            content_lower = message.content.lower()

            # ──────────────────────────────
            # Diagnose / evaluate priority – CHECK FIRST
            # ──────────────────────────────
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
                return  # ← STOP – no roast, no auto-detection

            # ──────────────────────────────
            # Roast only if NO eval keyword present
            # ──────────────────────────────
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

        # ───────────────────────────────────────
        # Auto-detection for non-mentioned messages
        # ───────────────────────────────────────
        if now - last_response_time < cooldown:
            await bot.process_commands(message)
            return

        category = classify_message(text)

        if category == "ATTACK":
            # For attacks, try to use attacker history if available
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
            await send_response(message.channel, message.author, roast)
            last_response_time = now

        elif category == "DISTRESS":
            support = generate_support()
            await send_response(message.channel, message.author, support)
            last_response_time = now

        # Always process prefix ! commands at the end
        await bot.process_commands(message)

# ───────────────────────────────────────
# Run bot
# ───────────────────────────────────────
bot.run(DISCORD_TOKEN)
