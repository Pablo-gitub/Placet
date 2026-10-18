import os
from typing import List, Optional, Dict, Any
import httpx


class PlacetClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        api_token: Optional[str] = None,
    ):
        # PLACET_API_URL (same name as the bridge); PLACET_BASE_URL is the old name.
        self.base_url = (
            base_url
            or os.environ.get("PLACET_API_URL")
            or os.environ.get("PLACET_BASE_URL")
            or "http://127.0.0.1:8000"
        ).rstrip("/")
        # Identity comes from the token. PLACET_AGENT_ID is optional: when set,
        # the backend checks it matches the token, catching misconfigured clients.
        self.agent_id = agent_id or os.environ.get("PLACET_AGENT_ID") or None
        self.api_token = api_token or os.environ.get("PLACET_API_TOKEN", "")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def ask_human(
        self,
        question: str,
        options: Optional[List[str]] = None,
        multiple: bool = False,
        context: Optional[str] = None,
        timeout_seconds: int = 180,
        priority: Optional[str] = None,
    ) -> Dict[str, Any]:
        timeout = max(5, min(int(timeout_seconds), 600))
        url = f"{self.base_url}/api/requests"
        payload = {
            "sender_id": self.agent_id,
            "recipient_id": "human",
            "question_type": (
                ("multi_choice" if multiple else "single_choice") if options else "confirmation"
            ),
            "prompt": question,
            "context": context,
            "options": options or [],
        }
        if priority:
            payload["priority"] = priority
        with httpx.Client(timeout=float(timeout + 5)) as client:
            resp = client.post(
                url,
                json=payload,
                headers=self._headers(),
                params={"wait_seconds": timeout},
            )
            resp.raise_for_status()
            return resp.json()

    def send_to_agent(
        self,
        target_agent: Optional[str],
        prompt: str,
        timeout_seconds: int = 180,
        priority: Optional[str] = None,
        target_session: Optional[str] = None,
        prompt_source: Optional[str] = None,
        target_node: Optional[str] = None,
        relation_type: Optional[str] = None,
        rule_acknowledged: bool = False,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        timeout = max(5, min(int(timeout_seconds), 600))
        url = f"{self.base_url}/api/requests"
        payload = {
            "sender_id": self.agent_id,
            "recipient_id": target_agent,
            "recipient_session_id": target_session,
            "target_node": target_node,
            "relation_type": relation_type,
            "rule_acknowledged": rule_acknowledged,
            "prompt_source": prompt_source,
            "question_type": "prompt_review",
            "prompt": prompt,
            "context": context,
            "options": [],
        }
        if priority:
            payload["priority"] = priority
        with httpx.Client(timeout=float(timeout + 5)) as client:
            resp = client.post(
                url,
                json=payload,
                headers=self._headers(),
                params={"wait_seconds": timeout},
            )
            if resp.status_code == 428:
                # The general rule must be read first: nothing was sent.
                return resp.json()
            resp.raise_for_status()
            return resp.json()

    def list_relations(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"session_id": session_id} if session_id else None
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/api/agents/me/relations",
                              headers=self._headers(), params=params)
            resp.raise_for_status()
            return resp.json()

    def reply(
        self,
        request_id: int,
        response: str,
        status: str = "completed",
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests/{request_id}/reply"
        payload = {"content": response}
        if self.agent_id:
            payload["responder_id"] = self.agent_id
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def ack(self, request_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests/{request_id}/ack"
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def list_agents(self) -> Dict[str, Any]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{self.base_url}/api/agents", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            sessions = client.get(f"{self.base_url}/api/sessions", headers=self._headers())
            if sessions.status_code == 200 and isinstance(sessions.json(), list):
                listed = [
                    {k: s.get(k) for k in ("id", "agent_id", "cwd", "title", "entrypoint",
                                           "state", "reachable", "last_seen_at")}
                    for s in sessions.json()
                ]
                # Sessions Placet can wake now come first; the others only
                # receive messages if they come back.
                data["sessions"] = [s for s in listed if s["reachable"]]
                data["unreachable_sessions"] = [s for s in listed if not s["reachable"]]
            return data

    def check_status(self, request_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests/{request_id}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
