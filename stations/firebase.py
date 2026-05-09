import os
import logging

logger = logging.getLogger(__name__)

_firebase_initialized = False

def _init_firebase():
    global _firebase_initialized
    if _firebase_initialized:
        return True
    try:
        from django.conf import settings
        from firebase_admin import credentials, initialize_app
        cred_path = os.path.join(settings.BASE_DIR, "firebase_key.json")
        if not os.path.exists(cred_path):
            logger.warning("[Firebase] firebase_key.json not found. Push notifications disabled.")
            return False
        cred = credentials.Certificate(cred_path)
        try:
            initialize_app(cred)
        except ValueError:
            # Already initialized
            pass
        _firebase_initialized = True
        return True
    except Exception as e:
        logger.warning(f"[Firebase] Failed to initialize: {e}. Push notifications disabled.")
        return False


def send_push_notification(token: str, title: str, body: str):
    if not _init_firebase():
        return  # Silently skip if Firebase is not configured
    try:
        from firebase_admin import messaging
        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=title, body=body)
        )
        messaging.send(message)
    except Exception as e:
        logger.warning(f"[Firebase] Failed to send notification: {e}")
