import os
import asyncio
from google.cloud import storage
import json
from app.core.config import settings

def get_storage_client():
    try:
        # Requires GOOGLE_APPLICATION_CREDENTIALS in env or default service account
        return storage.Client()
    except Exception:
        return None

async def upload_to_gcs(destination_blob_name: str, file_bytes: bytes):
    client = get_storage_client()
    if not client or not settings.GCS_BUCKET_NAME:
        print(f"[Mock Storage] Saving {destination_blob_name} ({len(file_bytes)} bytes)")
        return

    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(destination_blob_name)
    # google-cloud-storage is synchronous; run off the event loop so we don't
    # stall every concurrent WebSocket/HTTP request on this instance while the
    # upload is in flight.
    await asyncio.to_thread(
        blob.upload_from_string, file_bytes, content_type="audio/wav"
    )

async def log_interaction(session_id: str, user_id: str, query: str, response: str, user_language: str):
    """Logs conversation data securely to Cloud Storage JSON blob. This can trigger async Postgres summaries."""
    log_data = {
        "session_id": session_id,
        "user_id": user_id,
        "user_query": query,
        "ai_response": response,
        "detected_language": user_language
    }
    client = get_storage_client()
    if not client or not settings.GCS_BUCKET_NAME:
        print(f"[Mock Log] {json.dumps(log_data)}")
        return
        
    bucket = client.bucket(settings.GCS_BUCKET_NAME)
    blob = bucket.blob(f"logs/interactions/{session_id}.json")
    # Same reason as upload_to_gcs: never do sync I/O on the event loop.
    await asyncio.to_thread(
        blob.upload_from_string,
        json.dumps(log_data),
        content_type="application/json",
    )
