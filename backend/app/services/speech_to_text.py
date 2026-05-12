import io
import wave
from app.services.openai_client import get_openai_client

async def transcribe_audio(audio_content: bytes, project_id: str = "", location: str = "global") -> dict:
    """Transcribes audio using OpenAI Whisper (pcm16, 16kHz mono)."""

    if not audio_content or len(audio_content) < 100:
        print(f"[STT] Skipped: audio buffer too small ({len(audio_content)} bytes)", flush=True)
        return {"text": "", "language": "en"}

    client = get_openai_client()

    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(audio_content)

    wav_io.seek(0)
    wav_bytes = wav_io.read()

    print(f"[STT] Sending {len(wav_bytes)} byte WAV to Whisper...", flush=True)

    response = await client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.wav", wav_bytes, "audio/wav"),
        response_format="verbose_json"
    )

    print(f"[STT] Transcribed: '{response.text}' (lang: {response.language})", flush=True)
    return {
        "text": response.text,
        "language": response.language
    }
