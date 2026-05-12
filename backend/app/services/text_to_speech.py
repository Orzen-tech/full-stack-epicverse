from app.services.openai_client import get_openai_client

async def synthesize_speech(text: str, language_code: str) -> bytes:
    """Uses OpenAI Text-to-Speech to generate synthesized audio. Natively supports almost all languages."""
    client = get_openai_client()
    
    # OpenAI TTS voices are language-agnostic. 
    # 'alloy' is a great neutral voice. Others: 'echo', 'fable', 'onyx', 'nova', 'shimmer'
    voice_name = "alloy"
    
    # Optionally map certain target languages to specific voices for character flavor
    if language_code.startswith("es") or language_code.startswith("zh"):
        voice_name = "nova"
    elif language_code.startswith("ta") or language_code.startswith("hi"):
        voice_name = "shimmer"
        
    response = await client.audio.speech.create(
        model="tts-1",
        voice=voice_name,
        input=text,
        response_format="mp3" # MP3 is much smaller and faster to send than WAV
    )
    return response.content
