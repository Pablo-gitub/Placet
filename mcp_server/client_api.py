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
        self.base_url = (base_url or os.environ.get("PLACET_BASE_URL", "http://localhost:8000")).rstrip("/")
        self.agent_id = agent_id or os.environ.get("PLACET_AGENT_ID", "anonymous")
        self.api_token = api_token or os.environ.get("PLACET_API_TOKEN", "")

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Placet-Agent": self.agent_id,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    def ask_human(
        self,
        question: str,
        options: Optional[List[str]] = None,
        allow_custom_text: bool = True,
        context: Optional[str] = None,
        timeout_seconds: int = 180,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests"
        payload = {
            "sender_id": self.agent_id,
            "recipient_id": "human",
            "question_type": "single_choice" if (options and len(options) > 0) else "prompt_review",
            "prompt": question,
            "context": context,
            "options": options or [],
            "timeout_seconds": timeout_seconds,
        }
        with httpx.Client(timeout=float(timeout_seconds + 5)) as client:
            resp = client.post(
                url,
                json=payload,
                headers=self._headers(),
                params={"wait_seconds": timeout_seconds},
            )
            resp.raise_for_status()
            return resp.json()

    def send_to_agent(
        self,
        target_agent: str,
        prompt: str,
        context: Optional[str] = None,
        require_human_approval: bool = True,
        timeout_seconds: int = 180,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests"
        payload = {
            "sender_id": self.agent_id,
            "recipient_id": target_agent,
            "question_type": "prompt_review",
            "prompt": prompt,
            "context": context,
            "options": [],
            "timeout_seconds": timeout_seconds,
        }
        with httpx.Client(timeout=float(timeout_seconds + 5)) as client:
            resp = client.post(
                url,
                json=payload,
                headers=self._headers(),
                params={"wait_seconds": timeout_seconds},
            )
            resp.raise_for_status()
            return resp.json()

    def reply(
        self,
        request_id: int,
        response: str,
        status: str = "completed",
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests/{request_id}/reply"
        payload = {
            "responder_id": self.agent_id,
            "content": response,
        }
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def list_agents(self) -> Dict[str, Any]:
        url = f"{self.base_url}/api/agents"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    def check_status(self, request_id: int) -> Dict[str, Any]:
        url = f"{self.base_url}/api/requests/{request_id}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url, headers=self._headers())
            resp.raise_for_status()
            return resp.json()
