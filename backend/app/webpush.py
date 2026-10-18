"""
Web Push notifications to the installed PWA (iOS 16.4+, Android, desktop).

Unlike ntfy, tapping one opens the PWA itself, on the right request. The
messages travel through the browser vendor's push service (Apple, Google,
Mozilla), encrypted end to end; they still carry only a short title and no
prompt text, as with the public ntfy server.

VAPID keys are generated on first use and kept in the database.
"""
import asyncio
import base64
import json
import logging
from typing import Callable, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Only browser vendors' push services: the backend never posts elsewhere.
PUSH_SERVICES = ("push.apple.com", "fcm.googleapis.com", "android.googleapis.com",
                 "push.services.mozilla.com", "notify.windows.com")
TTL_SECONDS = 24 * 3600


def allowed_endpoint(endpoint: str) -> bool:
    url = urlparse(endpoint or "")
    host = (url.hostname or "").lower()
    return url.scheme == "https" and any(host == s or host.endswith("." + s) for s in PUSH_SERVICES)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class WebPush:
    def __init__(self, db, subject: str = "", send: Optional[Callable] = None):
        self.db = db
        # Apple requires a contact: an https URL or mailto.
        self.subject = subject if subject.startswith(("https://", "mailto:")) else "mailto:placet@localhost"
        self._send = send
        self._vapid = None

    def _keys(self):
        if self._vapid is not None:
            return self._vapid
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
        pem = self.db.get_setting("vapid_private_pem")
        if pem:
            vapid = Vapid.from_pem(pem.encode("ascii"))
        else:
            vapid = Vapid()
            vapid.generate_keys()
            pem = vapid.private_pem().decode("ascii")
            self.db.set_setting("vapid_private_pem", pem)
        raw = vapid.public_key.public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        self._vapid = (vapid, _b64url(raw))
        return self._vapid

    def public_key(self) -> str:
        return self._keys()[1]

    def _post(self, subscription: Dict, payload: str, urgency: str) -> int:
        """Sends one message; returns the push service's HTTP status."""
        if self._send is not None:
            return self._send(subscription, payload, urgency)
        from pywebpush import WebPushException, webpush
        vapid, _ = self._keys()
        try:
            resp = webpush(subscription_info=subscription, data=payload, vapid_private_key=vapid,
                           vapid_claims={"sub": self.subject}, ttl=TTL_SECONDS,
                           headers={"Urgency": urgency}, timeout=10)
            return resp.status_code
        except WebPushException as e:
            return e.response.status_code if e.response is not None else 0

    async def send(self, title: str, body: str, url: str, tag: str, urgency: str = "normal") -> int:
        """Sends to every subscribed device; forgets the ones that are gone."""
        subs = self.db.list_push_subscriptions()
        if not subs:
            return 0
        payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)
        sent = 0
        for sub in subs:
            try:
                status = await asyncio.to_thread(self._post, sub, payload, urgency)
            except Exception as e:
                logger.warning(f"Web Push failed: {e}")
                continue
            if status in (404, 410):
                self.db.delete_push_subscription(sub["endpoint"])
            elif 200 <= status < 300:
                sent += 1
            else:
                logger.warning(f"Web Push rejected: HTTP {status}")
        return sent

