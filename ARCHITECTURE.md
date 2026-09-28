# ARCHITECTURE — speech-mcp

## Deep Modules — Mikrofon → WhisperX (STT) · Text → TTS

Der Hauptflow nimmt über ALSA vom USB-Mikrofon auf, schickt das Audio an WhisperX und gibt den Text zurück; daneben Text-to-Speech über einen ComfyUI-kompatiblen Endpoint. Zwei Einstiege mit eigener Kopie derselben Logik: `server.py` (MCP stdio) und `http_server.py` (Starlette, Port 8902). Registry-Absicht: `dormant` (`~/repos/monitor-mcp/registry.yaml`). Jede Innenleben-Zelle ist datei:zeile und muss per grep -n treffen.

## Flow

**Sequenz** (MCP: `server.py:137 async def call_tool`)

| # | Modul | Eingang | Ausgang | Bedingung | Stellschraube | Innenleben |
|---|---|---|---|---|---|---|
| 1 | Aufnahme | Mikrofon | WAV in `RECORD_DIR` | ≤ 30 s | `MIC_DEVICE`, `MAX_RECORD_SECONDS` | `server.py:246 async def _record_sync` |
| 2 | Transkription | Audio-Datei | Text | — | `WHISPERX_URL` | `server.py:263 async def _transcribe_file` |
| 3 | TTS | Text | Audio | — | `TTS_URL` | `server.py:346 async def _handle_tts` |

**Parallel**

| Modul | Eingang | Ausgang | Bedingung | Stellschraube | Innenleben |
|---|---|---|---|---|---|
| HTTP-Einstieg | `/listen`, `/record/*`, `/transcribe*`, `/tts` | JSON | — | `SPEECH_PORT` (8902) | `http_server.py:250 Route("/health"` |
| Telegram-Voice | `file_id` | Text | — | — | `http_server.py:205 async def transcribe_telegram` |

## Schnittstellen

- Konfiguration nur über Env (`.env.example`).
- Verstoß gegen R2 (eine Fassade): Aufnahme und Transkription existieren doppelt — `http_server.py:82 async def _transcribe` neben `server.py:263 async def _transcribe_file`.

## Standard: Deep Modules + Flow

Kanon: `~/repos/speech-engine/ARCHITECTURE.md` § Standard (R1–R5).
