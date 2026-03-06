@bot.event
async def on_message(message):
    global last_response_time

    if message.author.bot:
        return

    # Optional: ignore DMs if you want (uncomment if needed)
    # if message.guild is None:
    #     return

    async with processing_lock:
        if message.id in processed_messages:
            return
        processed_messages.add(message.id)

        text = message.content.lower()
        now = asyncio.get_event_loop().time()

        # ───────────────────────────────────────
        # Bot mentioned → commands / special handling
        # ───────────────────────────────────────
        if bot.user in message.mentions:
            targets = [m for m in message.mentions if m.id != bot.user.id]

            if not targets:
                # Optional: reply something if just @bot with no target
                return

            target = targets[0]

            if "roast" in text:
                roast = generate_roast()
                await send_response(message.channel, target, roast)
                last_response_time = now
                return  # ← important: stop here

            if "eval" in text or "evaluate" in text:
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
                return  # ← stop here

        # ───────────────────────────────────────
        # Cooldown check (only for auto-responses)
        # ───────────────────────────────────────
        if now - last_response_time < cooldown:
            # Still allow commands even on cooldown
            await bot.process_commands(message)
            return

        # ───────────────────────────────────────
        # Auto detection / normal replies
        # ───────────────────────────────────────
        category = classify_message(text)

        if category == "ATTACK":
            roast = generate_roast()
            await send_response(message.channel, message.author, roast)
            last_response_time = now

        elif category == "DISTRESS":
            support = generate_support()
            await send_response(message.channel, message.author, support)
            last_response_time = now

        # Always process commands at the very end (prefix ! commands)
        await bot.process_commands(message)
