import sys
import os

# Ensure backend folder is in Python path so 'app' imports work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import pyaudio
import time
from app.services.wake_word import WakeWordDetector

def run_mic_test():
    print("\n" + "="*50)
    print("🔥 EpicVerse Wake Word Live Tester 🔥")
    print("="*50)
    print("Initializing ONNX Neural Networks...", flush=True)
    
    # Intialize our exact pipeline
    detector = WakeWordDetector()

    # Microphone settings (Must match what our ONNX model expects: 16kHz, Mono, 16-bit)
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 16000
    CHUNK = 1280  # 80ms chunks

    audio = pyaudio.PyAudio()

    print("\nConnecting to Microphone...", flush=True)
    try:
        stream = audio.open(format=FORMAT,
                            channels=CHANNELS,
                            rate=RATE,
                            input=True,
                            frames_per_buffer=CHUNK)
    except Exception as e:
        print(f"\n❌ Error connecting to microphone: {e}")
        print("Please check your microphone permissions or drivers.")
        sys.exit(1)

    print("\n🎤 LISTENING... Say 'Hey Epic' clearly into your mic! (Press Ctrl+C to stop)")
    print("-" * 50)

    try:
        while True:
            # Read 80ms of raw audio from the mic
            data = stream.read(CHUNK, exception_on_overflow=False)
            
            # Feed it into your ONNX pipeline
            result = detector.get_score(data)
            
            # Note: get_score() in wake_word.py already prints intermediate scores > 0.01!
            
            if result.get("detected"):
                print("\n" + "🎯"*5 + f" BOOM! TRUE POSITIVE TRIGGERED (Score: {result['score']:.3f}) " + "🎯"*5)
                print("Pausing for 3 seconds before listening again...\n")
                time.sleep(3)
                
                # Clear memory for next test
                detector.oww_model.reset()

    except KeyboardInterrupt:
        print("\n\n🛑 Testing stopped by user.")
    
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

if __name__ == "__main__":
    # Check for pyaudio
    try:
        import pyaudio
    except ImportError:
        print("❌ 'pyaudio' missing! Please install it by running: pip install pyaudio")
        sys.exit(1)
        
    run_mic_test()
