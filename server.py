#!/usr/bin/env python3
"""
Speech MCP Server - Unified STT/TTS interface for AIOT 4.0
Port: 8902
Backends: WhisperX (STT), Qwen3/Voicebox (TTS)
Input: Athom USB Mic (hw:1,0) or file upload
"""

import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Config
WHISPERX_URL = os.environ.get("WHISPERX_URL", "http://192.168.1.225:48001")
TTS_URL = os.environ.get("TTS_URL", "http://172.20.0.6:8188")
MIC_DEVICE = os.environ.get("MIC_DEVICE", "hw:1,0")
MIC_RATE = int(os.environ.get("MIC_RATE", "44100"))
MIC_CHANNELS = int(os.environ.get("MIC_CHANNELS", "2"))
RECORD_DIR = Path(os.environ.get("RECORD_DIR", "/tmp/speech-mcp"))
MAX_RECORD_SECONDS = 30

RECORD_DIR.mkdir(parents=True, exist_ok=True)

app = Server("speech")

# State
_recording_process = None
_recording_file = None
_state = "IDLE"  # IDLE, LISTENING, PROCESSING


def _set_state(new_state: str):
    global _state
    _state = new_state


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="speech__status",
            description="Get speech service status (mic, STT, TTS, state)",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="speech__record_start",
            description="Start recording from USB microphone. Returns immediately.",
            inputSchema={
                "type": "object",
                "properties": {
                    "max_seconds": {
                        "type": "integer",
                        "description": "Max recording duration (default 10, max 30)",
                        "default": 10,
                    }
                },
            },
        ),
        Tool(
            name="speech__record_stop",
            description="Stop recording and return the audio file path.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="speech__transcribe",
            description="Transcribe audio file via WhisperX. Accepts file path or records+transcribes if no path given.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to audio file. If omitted, records 5s from mic first.",
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Recording duration in seconds if no file_path (default 5)",
                        "default": 5,
                    },
                },
            },
        ),
        Tool(
            name="speech__transcribe_telegram",
            description="Download and transcribe a Telegram voice message by file_id.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "Telegram file_id"},
                    "bot_token": {"type": "string", "description": "Telegram bot token"},
                },
                "required": ["file_id", "bot_token"],
            },
        ),
        Tool(
            name="speech__tts",
            description="Text-to-speech via Qwen3/Voicebox. Returns audio file path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to speak"},
                    "voice": {
                        "type": "string",
                        "description": "Voice preset (default: default)",
                        "default": "default",
                    },
                },
                "required": ["text"],
            },
        ),
        Tool(
            name="speech__listen",
            description="One-shot: record from mic for N seconds, transcribe, return text. Combines record+transcribe.",
            inputSchema={
                "type": "object",
                "properties": {
                    "duration": {
                        "type": "integer",
                        "description": "Seconds to listen (default 5, max 30)",
                        "default": 5,
                    }
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "speech__status":
            return await _handle_status()
        elif name == "speech__record_start":
            return await _handle_record_start(arguments)
        elif name == "speech__record_stop":
            return await _handle_record_stop()
        elif name == "speech__transcribe":
            return await _handle_transcribe(arguments)
        elif name == "speech__transcribe_telegram":
            return await _handle_transcribe_telegram(arguments)
        elif name == "speech__tts":
            return await _handle_tts(arguments)
        elif name == "speech__listen":
            return await _handle_listen(arguments)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"ERROR: {e}")]


async def _handle_status():
    # Check WhisperX
    stt_ok = False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{WHISPERX_URL}/health")
            stt_ok = r.status_code == 200
    except Exception:
        pass

    # Check mic
    mic_ok = False
    try:
        result = subprocess.run(
            ["arecord", "-D", MIC_DEVICE, "--dump-hw-params"],
            capture_output=True, text=True, timeout=3
        )
        mic_ok = "CHANNELS" in (result.stdout + result.stderr)
    except Exception:
        pass

    status = {
        "state": _state,
        "mic": {"device": MIC_DEVICE, "online": mic_ok, "rate": MIC_RATE, "channels": MIC_CHANNELS},
        "stt": {"url": WHISPERX_URL, "online": stt_ok},
        "tts": {"url": TTS_URL},
        "record_dir": str(RECORD_DIR),
    }
    return [TextContent(type="text", text=json.dumps(status, indent=2))]


async def _handle_record_start(args: dict):
    global _recording_process, _recording_file

    if _state == "LISTENING":
        return [TextContent(type="text", text="Already recording. Stop first.")]

    max_sec = min(args.get("max_seconds", 10), MAX_RECORD_SECONDS)
    ts = int(time.time())
    wav_path = RECORD_DIR / f"rec_{ts}.wav"

    cmd = [
        "arecord", "-D", MIC_DEVICE,
        "-f", "S16_LE", "-r", str(MIC_RATE), "-c", str(MIC_CHANNELS),
        "-d", str(max_sec),
        str(wav_path),
    ]

    _recording_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _recording_file = wav_path
    _set_state("LISTENING")

    return [TextContent(type="text", text=json.dumps({
        "status": "recording",
        "file": str(wav_path),
        "max_seconds": max_sec,
        "device": MIC_DEVICE,
    }))]


async def _handle_record_stop():
    global _recording_process, _recording_file

    if _state != "LISTENING" or _recording_process is None:
        return [TextContent(type="text", text="Not recording.")]

    _recording_process.terminate()
    try:
        _recording_process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _recording_process.kill()

    wav_path = _recording_file
    _recording_process = None
    _recording_file = None
    _set_state("IDLE")

    if wav_path and wav_path.exists():
        size = wav_path.stat().st_size
        return [TextContent(type="text", text=json.dumps({
            "status": "stopped",
            "file": str(wav_path),
            "size_bytes": size,
        }))]
    return [TextContent(type="text", text="Recording stopped but no file found.")]


async def _record_sync(duration: int) -> Path:
    """Record synchronously for N seconds, return wav path."""
    ts = int(time.time())
    wav_path = RECORD_DIR / f"rec_{ts}.wav"

    proc = await asyncio.create_subprocess_exec(
        "arecord", "-D", MIC_DEVICE,
        "-f", "S16_LE", "-r", str(MIC_RATE), "-c", str(MIC_CHANNELS),
        "-d", str(duration),
        str(wav_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    return wav_path


async def _transcribe_file(file_path: Path) -> str:
    """Send audio file to WhisperX, return transcript."""
    _set_state("PROCESSING")
    try:
        audio_data = file_path.read_bytes()
        boundary = "----SpeechMCPBoundary"
        filename = file_path.name
        content_type = "audio/wav" if filename.endswith(".wav") else "audio/ogg"

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode() + audio_data + f"\r\n--{boundary}--\r\n".encode()

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{WHISPERX_URL}/transcribe",
                content=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            result = r.json()

        transcript = result.get("transcript", "").strip()
        if not transcript:
            segments = result.get("segments", [])
            transcript = " ".join(s.get("text", "") for s in segments).strip()

        return transcript or "[empty]"
    finally:
        _set_state("IDLE")


async def _handle_transcribe(args: dict):
    file_path = args.get("file_path")

    if file_path:
        p = Path(file_path)
        if not p.exists():
            return [TextContent(type="text", text=f"File not found: {file_path}")]
    else:
        duration = min(args.get("duration", 5), MAX_RECORD_SECONDS)
        _set_state("LISTENING")
        p = await _record_sync(duration)

    transcript = await _transcribe_file(p)
    return [TextContent(type="text", text=json.dumps({
        "transcript": transcript,
        "source": str(p),
        "size_bytes": p.stat().st_size if p.exists() else 0,
    }))]


async def _handle_transcribe_telegram(args: dict):
    file_id = args["file_id"]
    bot_token = args["bot_token"]

    _set_state("PROCESSING")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Get file path from Telegram
            r = await client.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}")
            file_info = r.json()
            tg_path = file_info["result"]["file_path"]

            # Download
            r = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{tg_path}")
            audio_data = r.content

        # Save to temp file
        tmp = RECORD_DIR / f"tg_{int(time.time())}.ogg"
        tmp.write_bytes(audio_data)

        transcript = await _transcribe_file(tmp)
        return [TextContent(type="text", text=json.dumps({
            "transcript": transcript,
            "source": "telegram",
            "file_id": file_id,
        }))]
    finally:
        _set_state("IDLE")


async def _handle_tts(args: dict):
    text = args["text"]
    voice = args.get("voice", "default")

    # TTS via Qwen3/Voicebox endpoint
    ts = int(time.time())
    out_path = RECORD_DIR / f"tts_{ts}.wav"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{TTS_URL}/tts",
                json={"text": text, "voice": voice},
            )
            if r.status_code == 200:
                out_path.write_bytes(r.content)
                return [TextContent(type="text", text=json.dumps({
                    "status": "ok",
                    "file": str(out_path),
                    "size_bytes": out_path.stat().st_size,
                }))]
            else:
                return [TextContent(type="text", text=f"TTS error: {r.status_code} {r.text[:200]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"TTS error: {e}")]


async def _handle_listen(args: dict):
    """One-shot: record + transcribe."""
    duration = min(args.get("duration", 5), MAX_RECORD_SECONDS)

    _set_state("LISTENING")
    wav_path = await _record_sync(duration)

    transcript = await _transcribe_file(wav_path)

    return [TextContent(type="text", text=json.dumps({
        "transcript": transcript,
        "duration": duration,
        "source": str(wav_path),
    }))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
