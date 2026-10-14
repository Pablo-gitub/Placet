import json
import logging
from typing import Optional
import httpx
from backend.app.models import RequestModel, RequestStatus

logger = logging.getLogger(__name__)


async def send_ntfy_notification(
    request: RequestModel,
    ntfy_topic: Optional[str] = None,
    base_url: str = "http://localhost:8000",
) -> bool:
    if not ntfy_topic:
        return False

    title = f"Placet: {request.sender_id} -> {request.recipient_id}"
    body = request.prompt[:200]
    if request.context:
        body = f"{request.context}\n\n{body}"

    headers = {
        "Title": title,
        "Priority": "high" if request.recipient_id == "human" else "default",
        "Tags": "robot,white_check_mark",
        "Actions": (
            f"view, Apri PWA, {base_url}/#request-{request.id}; "
            f"http, Approva, {base_url}/api/requests/{request.id}/decision, body={json.dumps({'status': 'APPROVED'})}; "
            f"http, Rifiuta, {base_url}/api/requests/{request.id}/decision, body={json.dumps({'status': 'REJECTED'})}"
        ),
    }

    url = f"https://ntfy.sh/{ntfy_topic}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, content=body.encode("utf-8"), headers=headers)
            return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Impossibile inviare notifica ntfy: {e}")
        return False
