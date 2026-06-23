    channel_memory_for(channel.id).append(("bot", safe_text))


# ───────────────────────────────────────
# Discord Events
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


@bot.event
async def on_message(message):
    try:
        await handle_message(message)
    except Exception:
        log.exception(
            "Unhandled message error channel=%s author=%s message=%s",
            getattr(getattr(message, "channel", None), "id", "unknown"),
            getattr(getattr(message, "author", None), "id", "unknown"),
            getattr(message, "id", "unknown"),
        )
        try:
            await bot.process_commands(message)
        except Exception:
            log.exception("Command processing also failed after message handler error")


async def handle_message(message):
    if message.author.bot:
        return

    # Let command handling run first for explicit commands.
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

        recent, older = await collect_target_messages(message.channel, target.id)

        profile = user_profiles.get(target.id, "No profile yet.")
        asyncio.create_task(refresh_profile_background(message.channel, target.id))

        reply = await generate_free_reply(original_text, channel_memory, recent, older, profile)
        await send_response(message.channel, target, reply)
        channel_last_response_time[channel_id] = now
        await bot.process_commands(message)
        return

    # ───── Automatic distress responses ─────
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
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, reconnect=True)
