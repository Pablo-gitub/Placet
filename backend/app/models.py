from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field, field_validator, model_validator


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DELIVERING = "DELIVERING"            # injected, waiting for the agent's ack
    EXECUTING = "EXECUTING"              # the agent confirmed it received the prompt
    DELIVERY_FAILED = "DELIVERY_FAILED"  # no ack after the maximum attempts
    STALLED = "STALLED"                  # executing for too long without a reply
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


# States after which a request can no longer change.
FINAL_STATUSES = frozenset({
    RequestStatus.REJECTED,
    RequestStatus.COMPLETED,
    RequestStatus.CANCELLED,
    RequestStatus.EXPIRED,
    RequestStatus.DELIVERY_FAILED,
    RequestStatus.STALLED,
})

# States still waiting for something to happen; they are subject to expiration.
OPEN_STATUSES = frozenset({
    RequestStatus.PENDING,
    RequestStatus.APPROVED,
    RequestStatus.DELIVERING,
    RequestStatus.EXECUTING,
})

# Outcome delivery to the sender's session (RF-22): wait this long so that a
# long-polling sender can receive it directly, and skip outcomes older than
# the max age (a session restarted hours later should not be flooded).
RESULT_GRACE_SECONDS = 10.0
RESULT_MAX_AGE_SECONDS = 6 * 3600

# Injection acknowledgement: without an ack within this time the injection
# is retried, up to the maximum attempts, then the request fails.
ACK_TIMEOUT_SECONDS = 90.0
MAX_DELIVERY_ATTEMPTS = 3
# An agent that declared itself busy is trusted for at most this long (a
# crashed CLI never sends "idle").
AGENT_BUSY_TTL_SECONDS = 30 * 60

# A session's background waiter long-polls its inbox for at most this long;
# without a poll for longer than the TTL the session is not reachable.
INBOX_WAIT_SECONDS = 240
WAITER_TTL_SECONDS = INBOX_WAIT_SECONDS + 60

DEFAULT_EXPIRES_IN_SECONDS = 24 * 3600
MAX_EXPIRES_IN_SECONDS = 7 * 24 * 3600


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"    # one option, or "Altro" with free text
    MULTI_CHOICE = "multi_choice"      # any options, plus optional "Altro"
    PROMPT_REVIEW = "prompt_review"    # agent-to-agent prompt the human may edit
    CONFIRMATION = "confirmation"      # yes/no question to the human, optional note


class Priority(str, Enum):
    """RF-07: decides how insistent the notification is."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


CHOICE_TYPES = frozenset({QuestionType.SINGLE_CHOICE, QuestionType.MULTI_CHOICE})


class ResponseModel(BaseModel):
    id: Optional[int] = None
    request_id: int
    responder_id: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # True when captured from the agent's last message at the end of its turn,
    # because it did not call placet_reply.
    implicit: bool = False



class RequestCreate(BaseModel):
    # Filled in by the backend from the authenticated agent; if a client sends
    # it, it must match.
    sender_id: Optional[str] = None
    recipient_id: str
    question_type: QuestionType = QuestionType.PROMPT_REVIEW
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    # Defaults to high for questions to the human, normal for agent-to-agent.
    priority: Optional[Priority] = None
    # Deliver to this session of the recipient agent (default: any of its
    # reachable sessions, or its tmux session).
    recipient_session_id: Optional[str] = None
    # Lifetime of the request, independent of how long the caller waits for it.
    expires_in_seconds: int = Field(
        DEFAULT_EXPIRES_IN_SECONDS, ge=10, le=MAX_EXPIRES_IN_SECONDS
    )

    @field_validator("options")
    @classmethod
    def _clean_options(cls, v: List[str]) -> List[str]:
        cleaned = [o.strip() for o in v]
        if any(not o for o in cleaned):
            raise ValueError("options must not be empty strings")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("options must be unique")
        return cleaned

    @model_validator(mode="after")
    def _options_match_type(self) -> "RequestCreate":
        if self.question_type in CHOICE_TYPES and not self.options:
            raise ValueError(f"{self.question_type.value} requires at least one option")
        if self.question_type not in CHOICE_TYPES and self.options:
            raise ValueError(f"{self.question_type.value} does not take options")
        return self


class RequestDecision(BaseModel):
    status: RequestStatus
    edited_prompt: Optional[str] = None
    selected_options: List[str] = Field(default_factory=list)
    custom_text: Optional[str] = None
    # For prompt review: deliver to this session of the recipient agent.
    recipient_session_id: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _only_human_decisions(cls, v: RequestStatus) -> RequestStatus:
        if v not in (RequestStatus.APPROVED, RequestStatus.REJECTED):
            raise ValueError("decision status must be APPROVED or REJECTED")
        return v

    @field_validator("custom_text", "edited_prompt")
    @classmethod
    def _blank_is_none(cls, v: Optional[str]) -> Optional[str]:
        return v if v is not None and v.strip() else None


class RequestModel(BaseModel):
    id: int
    sender_id: str
    recipient_id: str
    question_type: QuestionType
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    status: RequestStatus = RequestStatus.PENDING
    priority: Priority = Priority.NORMAL
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
    delivery_attempts: int = 0
    delivering_since: Optional[datetime] = None
    executing_since: Optional[datetime] = None
    # The agent session that made the request (outcome goes back there), and
    # the one chosen to receive it, if any.
    sender_session_id: Optional[str] = None
    recipient_session_id: Optional[str] = None


class SessionModel(BaseModel):
    """An agent session (e.g. one Claude Code conversation), from its hooks."""
    id: str
    agent_id: str
    provider: Optional[str] = None
    entrypoint: Optional[str] = None      # claude-desktop, cli, ...
    cwd: Optional[str] = None
    title: Optional[str] = None
    tmux_session: Optional[str] = None
    state: str = "idle"                   # busy while a turn is running
    turn_request_id: Optional[int] = None
    started_at: datetime
    last_seen_at: datetime
    waiter_seen_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    # Computed: a background waiter is listening, so Placet can wake it.
    reachable: bool = False


class RequestEvent(BaseModel):
    """One entry of the audit log (RF-13)."""
    id: int
    request_id: int
    at: datetime
    # "agent:<id>", "human:<tailscale login>", "bridge" or "system".
    actor: str
    # created, decided, injected, replied, cancelled, expired, delivered, delivery_failed
    event: str
    details: Dict[str, Any] = Field(default_factory=dict)


def decision_error(req: "RequestModel", decision: RequestDecision) -> Optional[str]:
    """
    Checks a decision against the question it answers (RF-02, RF-03).
    Returns a message describing the problem, or None if it is valid.
    """
    selected = decision.selected_options
    if decision.edited_prompt is not None and req.question_type != QuestionType.PROMPT_REVIEW:
        return "edited_prompt is only allowed when reviewing a prompt for an agent"
    if decision.recipient_session_id is not None and req.question_type != QuestionType.PROMPT_REVIEW:
        return "recipient_session_id is only allowed when reviewing a prompt for an agent"

    if req.question_type not in CHOICE_TYPES:
        if selected:
            return f"{req.question_type.value} has no options to select"
        return None

    if len(set(selected)) != len(selected):
        return "selected_options must be unique"
    unknown = [o for o in selected if o not in req.options]
    if unknown:
        return f"unknown options: {', '.join(unknown)}"
    if decision.status == RequestStatus.REJECTED:
        if selected:
            return "a rejection cannot select options"
        return None

    if req.question_type == QuestionType.SINGLE_CHOICE:
        if len(selected) + (1 if decision.custom_text else 0) != 1:
            return "single_choice needs exactly one option, or 'Altro' with a text"
    elif not selected and not decision.custom_text:
        return "multi_choice needs at least one option or 'Altro' with a text"
    return None


def is_settled(req: RequestModel) -> bool:
    """
    True when the sender has its answer: a human question is settled once
    decided; an agent-to-agent request only once it reaches a final state
    (the reply arrived, or it was rejected/cancelled/expired).
    """
    if req.recipient_id == "human":
        return req.status != RequestStatus.PENDING
    return req.status in FINAL_STATUSES

