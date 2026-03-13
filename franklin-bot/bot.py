#!/usr/bin/env python3
"""Discord bot for Radeon AI Cluster.
Uses slash commands for text chat and voice channels with STT/TTS."""

import os
import io
import asyncio
import logging
import tempfile
import traceback
import wave
from collections import defaultdict

import discord
from discord import app_commands
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("radeon-bot")

# Config from environment
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
VLLM_BASE = os.environ.get("VLLM_BASE_URL", "http://host.docker.internal:8000")
STT_BASE = os.environ.get("STT_BASE_URL", "http://speaches:8000")
TTS_BASE = os.environ.get("TTS_BASE_URL", "http://kokoro-tts:8880")
WORKER_CONTROL_URL = os.environ.get("WORKER_CONTROL_URL", "http://host.docker.internal:8096")
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "160637406985322496"))
MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen3-32B")
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT",
    "You are a helpful AI assistant running on a local Radeon AI Cluster. "
    "Keep responses concise and conversational. When in a voice channel, "
    "keep responses under 3 sentences unless asked for detail."
)
TTS_VOICE = os.environ.get("TTS_VOICE", "af_heart")
STT_MODEL = os.environ.get("STT_MODEL", "Systran/faster-whisper-small")
MAX_HISTORY = int(os.environ.get("MAX_HISTORY", "20"))

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True


class RadeonBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.channel_history: dict[int, list[dict]] = defaultdict(list)

    async def setup_hook(self):
        guild = discord.Object(id=DISCORD_GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        log.info("Syncing guild slash commands for %s...", DISCORD_GUILD_ID)
        synced = await self.tree.sync(guild=guild)
        log.info("Guild slash commands synced for %s: %s", DISCORD_GUILD_ID, len(synced))
        self.tree.clear_commands(guild=None)
        cleared = await self.tree.sync()
        log.info("Global slash commands cleared: %s", len(cleared))


bot = RadeonBot()


# --- Helpers ---

async def chat_completion(messages: list[dict], voice_mode: bool = False) -> str:
    import re as _re
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 8192 if not voice_mode else 1024,
        "stream": False,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{VLLM_BASE}/v1/chat/completions",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                log.error(f"vLLM error {resp.status}: {text}")
                return "Sorry, I couldn't get a response from the LLM."
            data = await resp.json()
            content = data["choices"][0]["message"]["content"]
            content = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
            return content


async def synthesize_speech(text: str) -> bytes | None:
    payload = {
        "model": "kokoro",
        "input": text,
        "voice": TTS_VOICE,
        "response_format": "wav",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{TTS_BASE}/v1/audio/speech",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                log.error(f"TTS error {resp.status}: {await resp.text()}")
                return None
            return await resp.read()


async def worker_control(method: str, path: str, payload: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.request(
            method,
            f"{WORKER_CONTROL_URL}{path}",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as resp:
            data = await resp.json()
            if resp.status >= 400:
                raise RuntimeError(data.get("error") or f"worker control error {resp.status}")
            return data


def build_messages(channel_id: int, user_msg: str) -> list[dict]:
    history = bot.channel_history[channel_id]
    history.append({"role": "user", "content": user_msg})
    if len(history) > MAX_HISTORY * 2:
        history[:] = history[-(MAX_HISTORY * 2):]
    return [{"role": "system", "content": SYSTEM_PROMPT}] + history


# --- Events ---

@bot.event
async def on_ready():
    log.info(f"Bot ready as {bot.user} (ID: {bot.user.id})")
    log.info(f"Guilds: {[g.name for g in bot.guilds]}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening, name="your questions"
    ))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_mentioned = bot.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not is_mentioned and not is_dm:
        return

    content = message.content
    for mention in message.mentions:
        content = content.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
    content = content.strip()

    if not content:
        content = "Hello!"

    async with message.channel.typing():
        messages = build_messages(message.channel.id, content)
        response = await chat_completion(messages)
        bot.channel_history[message.channel.id].append({"role": "assistant", "content": response})

        if len(response) <= 2000:
            await message.reply(response, mention_author=False)
        else:
            chunks = [response[i:i+1990] for i in range(0, len(response), 1990)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply(chunk, mention_author=False)
                else:
                    await message.channel.send(chunk)


# --- Slash Commands ---

@bot.tree.command(name="chat", description="Chat with the AI")
@app_commands.describe(message="Your message to the AI")
async def chat_cmd(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    messages = build_messages(interaction.channel_id, message)
    response = await chat_completion(messages)
    bot.channel_history[interaction.channel_id].append({"role": "assistant", "content": response})

    if len(response) <= 2000:
        await interaction.followup.send(response)
    else:
        chunks = [response[i:i+1990] for i in range(0, len(response), 1990)]
        await interaction.followup.send(chunks[0])
        for chunk in chunks[1:]:
            await interaction.channel.send(chunk)


@bot.tree.command(name="join", description="Join your voice channel")
async def join_cmd(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    log.info(f"/join from {interaction.user} -> {channel.name}")

    try:
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.move_to(channel)
        else:
            await channel.connect()

        await interaction.response.send_message(
            f"Joined **{channel.name}**. Use `/ask` to talk to me, or `/say` to hear TTS."
        )
    except Exception as e:
        log.error(f"Failed to join voice: {e}\n{traceback.format_exc()}")
        await interaction.response.send_message(f"Failed to join: {e}", ephemeral=True)


@bot.tree.command(name="leave", description="Leave the voice channel")
async def leave_cmd(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Left the voice channel.")
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)


@bot.tree.command(name="ask", description="Ask a question and hear the answer in voice")
@app_commands.describe(question="Your question")
async def ask_cmd(interaction: discord.Interaction, question: str):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc:
        await interaction.response.send_message(
            "I'm not in a voice channel. Use `/join` first.", ephemeral=True
        )
        return

    await interaction.response.defer()

    messages = build_messages(interaction.channel_id, question)
    response = await chat_completion(messages, voice_mode=True)
    bot.channel_history[interaction.channel_id].append({"role": "assistant", "content": response})

    await interaction.followup.send(response)

    audio_data = await synthesize_speech(response)
    if audio_data:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            tmp_path = f.name
        source = discord.FFmpegPCMAudio(tmp_path)
        if vc.is_playing():
            vc.stop()
        vc.play(source, after=lambda e: os.unlink(tmp_path))


@bot.tree.command(name="say", description="Speak text in the voice channel via TTS")
@app_commands.describe(text="Text to speak")
async def say_cmd(interaction: discord.Interaction, text: str):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc:
        await interaction.response.send_message(
            "I'm not in a voice channel. Use `/join` first.", ephemeral=True
        )
        return

    await interaction.response.defer()

    audio_data = await synthesize_speech(text)
    if not audio_data:
        await interaction.followup.send("TTS failed.", ephemeral=True)
        return

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_data)
        tmp_path = f.name

    source = discord.FFmpegPCMAudio(tmp_path)
    if vc.is_playing():
        vc.stop()
    vc.play(source, after=lambda e: os.unlink(tmp_path))
    preview = text[:100] + ('...' if len(text) > 100 else '')
    await interaction.followup.send(f"Speaking: *{preview}*")


@bot.tree.command(name="clear", description="Clear conversation history for this channel")
async def clear_cmd(interaction: discord.Interaction):
    bot.channel_history[interaction.channel_id].clear()
    await interaction.response.send_message("Conversation history cleared.")


@bot.tree.command(name="model", description="Show current model info")
async def model_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(f"Model: **{MODEL_NAME}**\nAPI: `{VLLM_BASE}`")


@bot.tree.command(name="status", description="Check AI cluster service health")
async def status_cmd(interaction: discord.Interaction):
    await interaction.response.defer()

    checks = {}
    async with aiohttp.ClientSession() as session:
        for name, url in [
            ("vLLM", f"{VLLM_BASE}/health"),
            ("STT (Speaches)", f"{STT_BASE}/health"),
            ("TTS (Kokoro)", f"{TTS_BASE}/health"),
        ]:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    checks[name] = r.status == 200
            except Exception:
                checks[name] = False

    lines = ["**Radeon AI Cluster -- Service Status**"]
    for name, ok in checks.items():
        icon = "\u2705" if ok else "\u274c"
        lines.append(f"{icon} {name}")

    vc = interaction.guild.voice_client if interaction.guild else None
    if vc and vc.is_connected():
        lines.append(f"\nIn voice: **{vc.channel.name}**")

    await interaction.followup.send("\n".join(lines))


@bot.tree.command(name="voice", description="Set TTS voice")
@app_commands.describe(voice="Voice name (e.g. af_heart, af_bella, af_sky, af_nova)")
async def voice_cmd(interaction: discord.Interaction, voice: str):
    global TTS_VOICE
    TTS_VOICE = voice
    await interaction.response.send_message(f"TTS voice set to **{voice}**")


@bot.tree.command(name="s-start", description="Open a media URL in the browser worker and start tab sharing it")
@app_commands.describe(url="YouTube, Rumble, or other HTML5 media URL", speed="Playback speed such as 1.0, 1.25, or 1.5")
async def s_start_cmd(interaction: discord.Interaction, url: str, speed: float = 1.0):
    await interaction.response.defer()
    try:
        result = await worker_control(
            "POST",
            "/stream/start",
            {"url": url, "speed": speed, "requestor": str(interaction.user)},
        )
        await interaction.followup.send(
            "Started stream share.\n"
            f"Source: {result.get('source_kind', 'unknown')}\n"
            f"Title: {result.get('title', 'unknown')}\n"
            f"Speed: {result.get('playback_speed', speed)}x\n"
            f"Streaming: {'yes' if result.get('streaming') else 'no'}"
        )
    except Exception as exc:
        await interaction.followup.send(f"Stream start failed: {exc}")


@bot.tree.command(name="s-stop", description="Stop the active Discord tab share on the browser worker")
async def s_stop_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        result = await worker_control("POST", "/stream/stop")
        await interaction.followup.send(
            f"Stream stop result: **{result.get('status', 'unknown')}**\n"
            f"Streaming: {'yes' if result.get('streaming') else 'no'}"
        )
    except Exception as exc:
        await interaction.followup.send(f"Stream stop failed: {exc}")


@bot.tree.command(name="s-swap", description="Replace the current broadcast tab URL without changing Discord/share state")
@app_commands.describe(url="The new YouTube or media URL to load into the existing broadcast tab", speed="Playback speed such as 1.0, 1.25, or 1.5")
async def s_swap_cmd(interaction: discord.Interaction, url: str, speed: float = 1.0):
    await interaction.response.defer()
    try:
        result = await worker_control(
            "POST",
            "/stream/swap",
            {"url": url, "speed": speed, "requestor": str(interaction.user)},
        )
        await interaction.followup.send(
            "Broadcast tab swapped.\n"
            f"Source: {result.get('source_kind', 'unknown')}\n"
            f"Title: {result.get('title', 'unknown')}\n"
            f"Speed: {result.get('playback_speed', speed)}x"
        )
    except Exception as exc:
        await interaction.followup.send(f"Swap failed: {exc}")


@bot.tree.command(name="s-speed", description="Set playback speed on the active browser-worker media tab")
@app_commands.describe(speed="Playback speed such as 1.0, 1.25, or 1.5")
async def s_speed_cmd(interaction: discord.Interaction, speed: float):
    await interaction.response.defer()
    try:
        result = await worker_control("POST", "/stream/speed", {"speed": speed})
        await interaction.followup.send(
            f"Playback speed set to **{result.get('playback_speed', speed)}x**\n"
            f"Title: {result.get('title', 'unknown')}"
        )
    except Exception as exc:
        await interaction.followup.send(f"Stream speed update failed: {exc}")


@bot.tree.command(name="s-status", description="Show current browser-worker share state")
async def s_status_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        result = await worker_control("GET", "/status")
        await interaction.followup.send(
            "**Browser Worker Status**\n"
            f"Share active: {'yes' if result.get('share_active') else 'no'}\n"
            f"Discord connected: {'yes' if result.get('discord_connected') else 'no'}\n"
            f"Discord login required: {'yes' if result.get('login_required') else 'no'}\n"
            f"Source: {result.get('source_kind') or 'unknown'}\n"
            f"Speed: {result.get('playback_speed') or 'n/a'}\n"
            f"Title: {result.get('active_title') or 'unknown'}\n"
            f"URL: {result.get('active_url') or 'none'}"
        )
    except Exception as exc:
        await interaction.followup.send(f"Status check failed: {exc}")


@bot.tree.command(name="s-play", description="Resume playback on the active browser-worker media tab")
async def s_play_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        result = await worker_control("POST", "/stream/play")
        await interaction.followup.send(
            f"Playback resumed.\n"
            f"Title: {result.get('title', 'unknown')}\n"
            f"Speed: {result.get('playback_speed') or 'n/a'}x"
        )
    except Exception as exc:
        await interaction.followup.send(f"Play failed: {exc}")


@bot.tree.command(name="s-pause", description="Pause playback on the active browser-worker media tab")
async def s_pause_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        result = await worker_control("POST", "/stream/pause")
        await interaction.followup.send(
            f"Playback paused.\n"
            f"Title: {result.get('title', 'unknown')}\n"
            f"At: {round(float(result.get('current_time') or 0), 1)}s"
        )
    except Exception as exc:
        await interaction.followup.send(f"Pause failed: {exc}")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
