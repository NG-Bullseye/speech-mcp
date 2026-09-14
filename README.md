# speech-mcp

Unified STT/TTS MCP-Server — speech-to-text via WhisperX, text-to-speech via ComfyUI/Qwen3, plus optional HTTP wrapper.

## Tools

| Tool | Description |
|---|---|
| `speech__status` | Service status (mic, STT, TTS, state) |
| `speech__record_start` / `speech__record_stop` | Start recording from USB microphone / stop and return the audio file path |
| `speech__transcribe` | Transcribe audio file via WhisperX (records first if no path given) |
| `speech__transcribe_telegram` | Download and transcribe a Telegram voice message by `file_id` |
| `speech__tts` | Text-to-speech via TTS endpoint |
| `speech__listen` | One-shot: record N seconds, transcribe, return text |

## Setup

```bash
cd speech-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # adjust WHISPERX_URL / TTS_URL / MIC_DEVICE
```

## MCP registration

```json
{
  "mcpServers": {
    "speech-mcp": {
      "command": ".venv/bin/python",
      "args": ["server.py"]
    }
  }
}
```

## HTTP mode (optional)

```bash
.venv/bin/python http_server.py
# Serves on http://localhost:8902
```

## Configuration

All config via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `WHISPERX_URL` | `localhost:48001` | WhisperX service endpoint |
| `TTS_URL` | `localhost:8188` | TTS service endpoint |
| `MIC_DEVICE` | `hw:1,0` | ALSA microphone device |
| `RECORD_DIR` | `/tmp/speech-mcp-recordings` | Recording scratch directory |

## License

MIT — see `LICENSE`.
