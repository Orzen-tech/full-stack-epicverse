from app.services.wake_word import WakeWordDetector
try:
    print("Initializing WakeWordDetector...")
    detector = WakeWordDetector()
    print("Success!")
except Exception as e:
    import traceback
    traceback.print_exc()
