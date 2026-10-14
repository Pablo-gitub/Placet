from enum import Enum
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# States after which a request can no longer change.
FINAL_STATUSES = frozenset({
    RequestStatus.REJECTED,
    RequestStatus.COMPLETED,
    RequestStatus.CANCELLED,
    RequestStatus.EXPIRED,
})

# States still waiting for something to happen; they are subject to expiration.
OPEN_STATUSES = frozenset({
    RequestStatus.PENDING,
    RequestStatus.APPROVED,
    RequestStatus.EXECUTING,
})

DEFAULT_EXPIRES_IN_SECONDS = 24 * 3600
MAX_EXPIRES_IN_SECONDS = 7 * 24 * 3600


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    PROMPT_REVIEW = "prompt_review"
    CONFIRMATION = "confirmation"


class ResponseModel(BaseModel):
    id: Optional[int] = None
    request_id: int
    responder_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))



class RequestCreate(BaseModel):
    # Filled in by the backend from the authenticated agent; if a client sends
    # it, it must match.
    sender_id: Optional[str] = None
    recipient_id: str
    question_type: QuestionType = QuestionType.PROMPT_REVIEW
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    # Lifetime of the request, independent of how long the caller waits for it.
    expires_in_seconds: int = Field(
        DEFAULT_EXPIRES_IN_SECONDS, ge=10, le=MAX_EXPIRES_IN_SECONDS
    )


class RequestDecision(BaseModel):
    status: RequestStatus
    edited_prompt: Optional[str] = None
    selected_options: List[str] = Field(default_factory=list)
    custom_text: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _only_human_decisions(cls, v: RequestStatus) -> RequestStatus:
        if v not in (RequestStatus.APPROVED, RequestStatus.REJECTED):
            raise ValueError("decision status must be APPROVED or REJECTED")
        return v


class RequestModel(BaseModel):
    id: int
    sender_id: str
    recipient_id: str
    question_type: QuestionType
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    status: RequestStatus = RequestStatus.PENDING
    edited_prompt: Optional[str] = None
    selected_options: List[str] = Field(default_factory=list)
    custom_text: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    decided_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    response: Optional[ResponseModel] = None
    # Last status change, and when the outcome reached the sender (through
    # long-polling or a bridge injection into its session).
    updated_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None


def is_settled(req: RequestModel) -> bool:
    """
    True when the sender has its answer: a human question is settled once
    decided; an agent-to-agent request only once it reaches a final state
    (the reply arrived, or it was rejected/cancelled/expired).
    """
    if req.recipient_id == "human":
        return req.status != RequestStatus.PENDING
    return req.status in FINAL_STATUSES

