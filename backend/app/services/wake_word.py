import os
import numpy as np
import onnxruntime as ort
from collections import deque

# --- PATHS (Exactly where the synced models are) ---
_BASE_DIR = r"e:\kriyora\EpicVerse\frontend\EpicVerseApp\assets\wakeword"
MEL_PATH = os.path.join(_BASE_DIR, "melspectrogram.onnx")
EMB_PATH = os.path.join(_BASE_DIR, "embedding_model.onnx")
WAKE_PATH = os.path.join(_BASE_DIR, "hey_epic_hyper.onnx")

import openwakeword
from openwakeword.model import Model

# --- PATHS (Exactly where the synced models are) ---
_BASE_DIR = r"e:\kriyora\EpicVerse\frontend\EpicVerseApp\assets\wakeword"
WAKE_PATH = os.path.join(_BASE_DIR, "hey_epic_hyper.onnx")

class WakeWordDetector:
    def __init__(self):
        print(f"[WakeWord] 🚀 Initializing Hardware-Gated Engine (Hyper + Jarvis)...", flush=True)
        # 1. Load sequence engine (VAD Disabled for faster load)
        self.oww_model = Model(
            wakeword_models=[os.path.join(_BASE_DIR, "hey_jarvis.onnx")],
            inference_framework="onnx",
            melspec_model_path=os.path.join(_BASE_DIR, "melspectrogram.onnx"),
            embedding_model_path=os.path.join(_BASE_DIR, "embedding_model.onnx")
        )
        # 2. Hyper Expert
        self.hyper_sess = ort.InferenceSession(WAKE_PATH, providers=["CPUExecutionProvider"])
        
        self.audio_buffer = np.array([], dtype=np.int16)
        print(f"[WakeWord] ✅ Real-time Listener Ready!", flush=True)

    def get_score(self, audio_data: bytes) -> dict:
        """Processes raw 16kHz audio bytes through hardware-gated chain."""
        try:
            raw_int16 = np.frombuffer(audio_data, dtype=np.int16)
            if len(raw_int16) == 0: return {"detected": False, "score": 0.0, "peak": 0}
            peak = int(np.max(np.abs(raw_int16)))
            
            # Step 1: Pre-process with Official OWW
            self.oww_model.predict(raw_int16) 
            jarvis_score = float(self.oww_model.prediction_buffer.get("hey_jarvis", deque([0.0]))[-1])
            
            # Step 2: Expert Hyper-Model Prediction (With Normalization)
            feats_raw = self.oww_model.preprocessor.get_features(1).reshape(1, 96)
            hyper_score = 0.0
            
            # Hardware Gate Check (Peak > 2500)
            if peak > 2500 and not np.all(feats_raw == 0):
                norm = np.linalg.norm(feats_raw, axis=1, keepdims=True) + 1e-6
                feats_norm = feats_raw / norm
                hyper_score = float(self.hyper_sess.run(None, {self.hyper_sess.get_inputs()[0].name: feats_norm})[0].squeeze())
            
            # Final scoring
            score = max(hyper_score, jarvis_score)
            
            # TRIGGER CONDITION: Look for a tiny jump over the 0.665 silence bias
            # Or use the proven Jarvis trigger (0.3)
            detected = (hyper_score > 0.668) or (jarvis_score > 0.3)
            
            if score > 0.665:
                print(f"[WakeWord] Prob: {score:.4f} (Jarvis: {jarvis_score:.4f}, Hyper: {hyper_score:.4f}) Pk: {peak}", flush=True)
            
            if detected:
                # Identification
                source = "Hyper" if (hyper_score > 0.668) else "Jarvis"
                print(f"🎯 [WakeWord] {source.upper()} TRIGGERED! (score={score:.4f})", flush=True)
                
            return {"detected": detected, "score": score, "peak": peak}
        except Exception as e:
            print(f"[WakeWord] Predict Error: {e}")
            return {"detected": False, "score": 0.0, "peak": 0}
