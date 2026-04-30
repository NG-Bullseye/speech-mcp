#!/usr/bin/env python3
"""
Speech MCP - HTTP API wrapper (Port 8902)
Exposes speech tools as REST endpoints for CYD/ESPHome/HA consumers.

Endpoints:
  GET  /health              - Service status
  GET  /status              - Detailed status (mic, STT, TTS)
  POST /listen              - Record + transcribe (one-shot)
  POST /record/start        - Start recording
  POST /record/stop         - Stop recording + return path
  POST /transcribe          - Transcribe audio file (multipart upload)
  POST /transcribe/telegram - Transcribe Telegram voice by file_id
  POST /tts                 - Text-to-speech
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, FileResponse
from starlette.routing import Route

# Config
WHISPERX_URL = os.environ.get("WHISPERX_URL", "http://192.168.1.225:48001")
TTS_URL = os.environ.get("TTS_URL", "http://172.20.0.6:8188")
MIC_DEVICE = os.environ.get("MIC_DEVICE", "hw:1,0")
MIC_RATE = int(os.environ.get("MIC_RATE", "44100"))
MIC_CHANNELS = int(os.environ.get("MIC_CHANNELS", "2"))
RECORD_DIR = Path(os.environ.get("RECORD_DIR", "/tmp/speech-mcp"))
MAX_RECORD_SECONDS = 30
PORT = int(os.environ.get("SPEECH_PORT", "8902"))

RECORD_DIR.mkdir(parents=True, exist_ok=True)

# State
_recording_process = None
_recording_file = None
_state = "IDLE"


async def _check_whisperx() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{WHISPERX_URL}/health")
            return r.status_code == 200
    except Exception:
        return False


async def _check_mic() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "arecord", "-D", MIC_DEVICE, "--dump-hw-params",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=3)
        return b"CHANNELS" in stdout + stderr
    except Exception:
        return False


async def _record(duration: int) -> Path:
    ts = int(time.time())
    wav_path = RECORD_DIR / f"rec_{ts}.wav"
    proc = await asyncio.create_subprocess_exec(
        "arecord", "-D", MIC_DEVICE,
        "-f", "S16_LE", "-r", str(MIC_RATE), "-c", str(MIC_CHANNELS),
        "-d", str(duration), str(wav_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    return wav_path


async def _transcribe(audio_data: bytes, filename: str = "audio.wav") -> str:
    boundary = "----SpeechHTTPBoundary"
    ct = "audio/wav" if filename.endswith(".wav") else "audio/ogg"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {ct}\r\n\r\n"
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
    return transcript or ""


# Routes

async def health(request: Request):
    return JSONResponse({"service": "speech-mcp", "version": "1.0.0", "state": _state})


async def status(request: Request):
    stt_ok = await _check_whisperx()
    mic_ok = await _check_mic()
    return JSONResponse({
        "state": _state,
        "mic": {"device": MIC_DEVICE, "online": mic_ok},
        "stt": {"url": WHISPERX_URL, "online": stt_ok},
        "tts": {"url": TTS_URL},
    })


async def listen(request: Request):
    global _state
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    duration = min(body.get("duration", 5), MAX_RECORD_SECONDS)

    _state = "LISTENING"
    wav_path = await _record(duration)
    _state = "PROCESSING"

    transcript = await _transcribe(wav_path.read_bytes(), wav_path.name)
    _state = "IDLE"

    return JSONResponse({
        "transcript": transcript,
        "duration": duration,
        "file": str(wav_path),
    })


async def record_start(request: Request):
    global _recording_process, _recording_file, _state

    if _state == "LISTENING":
        return JSONResponse({"error": "Already recording"}, status_code=409)

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    max_sec = min(body.get("max_seconds", 10), MAX_RECORD_SECONDS)

    ts = int(time.time())
    wav_path = RECORD_DIR / f"rec_{ts}.wav"

    _recording_process = await asyncio.create_subprocess_exec(
        "arecord", "-D", MIC_DEVICE,
        "-f", "S16_LE", "-r", str(MIC_RATE), "-c", str(MIC_CHANNELS),
        "-d", str(max_sec), str(wav_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _recording_file = wav_path
    _state = "LISTENING"

    return JSONResponse({"status": "recording", "file": str(wav_path), "max_seconds": max_sec})


async def record_stop(request: Request):
    global _recording_process, _recording_file, _state

    if _state != "LISTENING" or _recording_process is None:
        return JSONResponse({"error": "Not recording"}, status_code=409)

    _recording_process.terminate()
    try:
        await asyncio.wait_for(_recording_process.wait(), timeout=3)
    except asyncio.TimeoutError:
        _recording_process.kill()

    wav_path = _recording_file
    _recording_process = None
    _recording_file = None
    _state = "IDLE"

    if wav_path and wav_path.exists():
        return JSONResponse({"status": "stopped", "file": str(wav_path), "size_bytes": wav_path.stat().st_size})
    return JSONResponse({"error": "No file"}, status_code=500)


async def transcribe(request: Request):
    global _state
    form = await request.form()
    upload = form.get("file")
    if not upload:
        return JSONResponse({"error": "No file uploaded"}, status_code=400)

    audio_data = await upload.read()
    filename = getattr(upload, "filename", "audio.wav") or "audio.wav"

    _state = "PROCESSING"
    transcript = await _transcribe(audio_data, filename)
    _state = "IDLE"

    return JSONResponse({"transcript": transcript})


async def transcribe_telegram(request: Request):
    global _state
    body = await request.json()
    file_id = body.get("file_id")
    bot_token = body.get("bot_token")

    if not file_id or not bot_token:
        return JSONResponse({"error": "file_id and bot_token required"}, status_code=400)

    _state = "PROCESSING"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}")
            tg_path = r.json()["result"]["file_path"]
            r = await client.get(f"https://api.telegram.org/file/bot{bot_token}/{tg_path}")
            audio_data = r.content

        transcript = await _transcribe(audio_data, "voice.ogg")
        return JSONResponse({"transcript": transcript, "file_id": file_id})
    finally:
        _state = "IDLE"


async def tts(request: Request):
    body = await request.json()
    text = body.get("text", "")
    voice = body.get("voice", "default")

    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{TTS_URL}/tts", json={"text": text, "voice": voice})
            if r.status_code == 200:
                ts = int(time.time())
                out_path = RECORD_DIR / f"tts_{ts}.wav"
                out_path.write_bytes(r.content)
                return FileResponse(str(out_path), media_type="audio/wav")
            return JSONResponse({"error": f"TTS backend: {r.status_code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


routes = [
    Route("/health", health, methods=["GET"]),
    Route("/status", status, methods=["GET"]),
    Route("/listen", listen, methods=["POST"]),
    Route("/record/start", record_start, methods=["POST"]),
    Route("/record/stop", record_stop, methods=["POST"]),
    Route("/transcribe", transcribe, methods=["POST"]),
    Route("/transcribe/telegram", transcribe_telegram, methods=["POST"]),
    Route("/tts", tts, methods=["POST"]),
]

app = Starlette(routes=routes)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
