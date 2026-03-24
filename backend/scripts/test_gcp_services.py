import os
from google.cloud import speech_v2
from google.cloud import texttospeech
from dotenv import load_dotenv
import json

load_dotenv()

def test_tts():
    print("\n--- Testing Text-to-Speech ---")
    try:
        client = texttospeech.TextToSpeechClient()
        synthesis_input = texttospeech.SynthesisInput(text="Hello, this is a test.")
        voice = texttospeech.VoiceSelectionParams(
            language_code="en-US", ssml_gender=texttospeech.SsmlVoiceGender.NEUTRAL
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3
        )
        response = client.synthesize_speech(
            input=synthesis_input, voice=voice, audio_config=audio_config
        )
        print(f"SUCCESS: TTS generated {len(response.audio_content)} bytes of audio.")
    except Exception as e:
        print(f"FAILED TTS: {e}")

def test_stt():
    print("\n--- Testing Speech-to-Text V2 ---")
    try:
        # Note: We need a project ID for V2
        with open(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"), 'r') as f:
            creds = json.load(f)
        project_id = creds.get("project_id")
        
        client = speech_v2.SpeechClient()
        # Just check if we can list recognizers to see if auth works
        parent = f"projects/{project_id}/locations/global"
        recognizers = client.list_recognizers(parent=parent)
        print("SUCCESS: STT Auth works (listed recognizers).")
    except Exception as e:
        print(f"FAILED STT: {e}")

if __name__ == "__main__":
    print(f"Using credentials from: {os.getenv('GOOGLE_APPLICATION_CREDENTIALS')}")
    test_tts()
    test_stt()
