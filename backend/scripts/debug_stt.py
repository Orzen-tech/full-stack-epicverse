import asyncio
import os
import glob
from app.services.speech_to_text import transcribe_audio

async def main():
    try:
        latest_file = max(glob.glob('inputs/*.wav') + glob.glob('inputs/*.webm'), key=os.path.getctime)
        print(f'Using {latest_file}')
        with open(latest_file, 'rb') as f:
            audio = f.read()
        res = await transcribe_audio(audio)
        print("RESULT:")
        print(res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
