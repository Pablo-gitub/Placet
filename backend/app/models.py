from enum import Enum
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime, timezone
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator


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

# Injection acknowledgement: without an ack within this time a tmux injection
# is retried, up to the maximum attempts, then the request fails. A message
# taken by a session's waiter or queued by the bridge is not re-sent: its
# delivery becomes uncertain and the user decides (RF-29).
ACK_TIMEOUT_SECONDS = 90.0
MAX_DELIVERY_ATTEMPTS = 3
# One request at a time per session (RF-29b). Time never frees the queue: a
# delivered request whose turn is not seen starting within this long only
# raises a warning ("start unconfirmed"); the queue moves on with a proof
# that the turn ended, or when the user says so.
START_UNCONFIRMED_SECONDS = 120.0
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
    MESSAGE = "message"                # the user writes to a session from the PWA (RF-34)


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
    recipient_id: Optional[str] = None  # or `target_node`
    question_type: QuestionType = QuestionType.PROMPT_REVIEW
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    # File the prompt was read from (e.g. "PROMPT_GEMINI.md"), for display.
    prompt_source: Optional[str] = Field(None, max_length=255)
    # Defaults to high for questions to the human, normal for agent-to-agent.
    priority: Optional[Priority] = None
    # Deliver to this session of the recipient agent (default: any of its
    # reachable sessions, or its tmux session).
    recipient_session_id: Optional[str] = None
    # The calling session, when the agent knows it; otherwise the backend
    # takes the agent's only busy session.
    sender_session_id: Optional[str] = Field(None, max_length=100)
    # RFC-08: the relation type the sender means; needed when A -> B has
    # relations of several types.
    relation_type_id: Optional[int] = None
    # RFC-08: address a node by name and a relation type by name.
    target_node: Optional[str] = Field(None, max_length=40)
    relation_type: Optional[str] = Field(None, max_length=40)
    # RFC-15: project context and ID for project-associated requests
    project_id: Optional[int] = None
    project_context: Optional[str] = None
    # RFC-06: the sender read the general rule for automatic relations.
    rule_acknowledged: bool = False
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


class OutcomeDecision(BaseModel):
    approve: bool
    edited_response: Optional[str] = None
    # Forward the reply to this session instead of the sender (who gets a notice).
    forward_session_id: Optional[str] = Field(None, max_length=200)


class RequestDecision(BaseModel):
    allow_auto_return: bool = False
    status: RequestStatus
    edited_prompt: Optional[str] = None
    selected_options: List[str] = Field(default_factory=list)
    custom_text: Optional[str] = None
    # For prompt review: deliver to this session of the recipient agent.
    recipient_session_id: Optional[str] = None
    delivery_mode: Literal["queue", "now"] = "queue"

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
    allow_auto_return: bool = False
    return_decision: Optional[str] = None
    return_text: Optional[str] = None
    # Who let the prompt go: "automatic" (a rule or relation) or "manual" (the user).
    approval: Optional[str] = None
    # RFC-14: the request whose turn sent this one; consecutive automatic hops.
    parent_request_id: Optional[int] = None
    chain_hops: int = 0
    # The reply went, by the user's choice, to another session on this request.
    return_forward_id: Optional[int] = None
    # RFC-04: the relation the request travelled on, if any.
    relation_id: Optional[int] = None
    # RFC-15: project context and ID for project-associated requests
    project_id: Optional[int] = None
    project_context: Optional[str] = None
    id: int
    sender_id: str
    recipient_id: str
    question_type: QuestionType
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    status: RequestStatus = RequestStatus.PENDING
    priority: Priority = Priority.NORMAL
    prompt_source: Optional[str] = None
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
    # Three independent aspects of an agent request (RF-29), with their proofs:
    # delivery:  waiting, in_progress, accepted, uncertain, failed, cancelled
    # execution: unobserved, started, completed, interrupted
    # reply:     none, explicit, implicit, ambiguous
    delivery: str = "waiting"
    delivery_at: Optional[datetime] = None
    delivery_channel: Optional[str] = None
    attempt_id: Optional[str] = None
    execution: str = "unobserved"
    execution_at: Optional[datetime] = None
    # Who observed the execution: hook or adapter, with its version.
    execution_source: Optional[str] = None
    reply: str = "none"
    # The turn was not seen starting in time (a warning, the queue still waits).
    start_unconfirmed_at: Optional[datetime] = None
    # The user let the session's queue move on past this request.
    queue_released_at: Optional[datetime] = None
    # "queue": at the end of the recipient's turn; "now": inside the running
    # turn, after its next action (RF-35, "Subito").
    delivery_mode: str = "queue"


# Providers whose sessions the bridge wakes from outside (RF-24): Codex with
# `codex queue`, Antigravity through its app's language server. Claude Code
# sessions are woken by their own background waiter instead.
BRIDGE_PROVIDERS = ("codex", "antigravity")
# SessionEnd only means the app unloaded the conversation (idle, restart,
# update): it can be resumed, so the session is suspended, not ended.
SUSPENDING_PROVIDERS = ("codex",)


class SessionMention(BaseModel):
    session_id: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=2, max_length=600)


class SessionUpload(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data: str = Field(min_length=1, max_length=13981016)


# Providers whose running turn can take a message "now" (RF-35): Claude Code
# through its PostToolUse hook, Antigravity through its language server.
# Codex: not verified yet, its "now" messages wait for the end of the turn.
STEER_PROVIDERS = ("claude-code", "antigravity")


class SessionMessage(BaseModel):
    """A message the user writes to a session (RF-34)."""
    mode: Literal["queue", "now"] = "queue"
    text: str = Field(default="", max_length=20000)
    attachments: List[SessionUpload] = Field(default_factory=list, max_length=4)
    mentions: List[SessionMention] = Field(default_factory=list, max_length=20)
    # RFC-15: optional project context when sending message from PWA
    project_id: Optional[int] = None

    @model_validator(mode="after")
    def has_content(self):
        if not self.text.strip() and not self.attachments:
            raise ValueError("Write a message or select an attachment.")
        return self


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
    suspended_at: Optional[datetime] = None
    model: Optional[str] = None
    # Last turn start or end: orders the PWA home by activity (RF-40).
    activity_at: Optional[datetime] = None
    # Added by the user to the PWA home (RF-41).
    pinned: bool = False
    # Computed: a background waiter is listening, so Placet can wake it.
    reachable: bool = False
    # Its project has a `.placet-off` (RF-45): hidden, nothing delivered; the
    # data stays for the audit.
    excluded: bool = False
    # RFC-02: the stable named node this conversation belongs to.
    node_id: Optional[int] = None
    node_name: Optional[str] = None
    # RF-29d: continued conversation pointer and prompt counter
    continued_to: Optional[str] = None
    prompt_count: int = 0


class NodeModel(BaseModel):
    """A stable named session (RFC-02): survives `/clear` and reopening."""
    id: int
    name: str
    provider: str
    cwd: Optional[str] = None
    created_at: datetime
    # The attached session seen most recently, if any.
    session_id: Optional[str] = None
    # RFC-15: node instructions within a project (when viewed in project context)
    instructions: Optional[str] = None


class RelationTypeModel(BaseModel):
    """RFC-03: why one session contacts another; agents read the description."""
    id: int
    name: str
    description: str = ""
    created_at: datetime


class RelationModel(BaseModel):
    """RFC-04: a directed relation A -> B of one type, with its approval policy."""
    id: int
    from_node: int
    to_node: int
    type_id: int
    approval: Literal["manual", "automatic"]
    created_at: datetime
    from_name: str
    to_name: str
    type_name: str
    type_description: str = ""


class ProjectModel(BaseModel):
    """RFC-09: a project grouping nodes and relations."""
    id: int
    name: str
    description: str = ""
    relation_rule: Optional[str] = None
    max_auto_hops: Optional[int] = None
    max_auto_per_hour: Optional[int] = None
    # RFC-15: project instructions for context injection
    instructions: Optional[str] = None
    created_at: datetime


class ProjectSummaryModel(ProjectModel):
    nodes_count: int = 0
    relations_count: int = 0
    automatic_relations_count: int = 0


class ProjectRelationDetail(RelationModel):
    shared_with: List[str] = Field(default_factory=list)


class ProjectDetailModel(ProjectModel):
    nodes: List[NodeModel] = Field(default_factory=list)
    relations: List[ProjectRelationDetail] = Field(default_factory=list)
    effective_rule: Optional[str] = None
    effective_max_hops: Optional[int] = None
    effective_max_per_hour: Optional[int] = None


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


def decision_problem(req: "RequestModel", decision: RequestDecision) -> Optional[tuple[str, str]]:
    """Return a stable error code and English diagnostic, or None (RF-02/03)."""
    selected = decision.selected_options
    if decision.edited_prompt is not None and req.question_type != QuestionType.PROMPT_REVIEW:
        return "decision.prompt_edit_not_allowed", "edited_prompt is only allowed when reviewing a prompt for an agent"
    if decision.recipient_session_id is not None and req.question_type != QuestionType.PROMPT_REVIEW:
        return "decision.recipient_not_allowed", "recipient_session_id is only allowed when reviewing a prompt for an agent"

    if req.question_type not in CHOICE_TYPES:
        if selected:
            return "decision.no_options", f"{req.question_type.value} has no options to select"
        return None

    if len(set(selected)) != len(selected):
        return "decision.duplicate_options", "selected_options must be unique"
    unknown = [o for o in selected if o not in req.options]
    if unknown:
        return "decision.unknown_options", f"unknown options: {', '.join(unknown)}"
    if decision.status == RequestStatus.REJECTED:
        if selected:
            return "decision.rejected_options", "a rejection cannot select options"
        return None

    if req.question_type == QuestionType.SINGLE_CHOICE:
        if len(selected) + (1 if decision.custom_text else 0) != 1:
            return "decision.single_choice", "single_choice needs exactly one option, or Other with a text"
    elif not selected and not decision.custom_text:
        return "decision.multi_choice", "multi_choice needs at least one option or Other with a text"
    return None


def decision_error(req: "RequestModel", decision: RequestDecision) -> Optional[str]:
    """Compatibility helper for callers that only need the diagnostic string."""
    problem = decision_problem(req, decision)
    return problem[1] if problem else None


def is_settled(req: RequestModel) -> bool:
    """
    True when the sender has its answer: a human question is settled once
    decided; an agent-to-agent request only once it reaches a final state
    (the reply arrived, or it was rejected/cancelled/expired).
    """
    if req.recipient_id == "human":
        return req.status != RequestStatus.PENDING
    return req.status in FINAL_STATUSES


class UsageBucket(BaseModel):
    id: str = Field(max_length=100)
    group: Optional[str] = Field(default=None, max_length=100)
    window: Literal["5h", "7d"]
    used_percent: float = Field(ge=0, le=100)
    resets_at: Optional[AwareDatetime] = None


class ProviderUsage(BaseModel):
    """RF-60: how much of a provider's usage windows is used, as the bridge read it."""
    provider: Literal["claude-code", "codex", "antigravity"]
    plan: Optional[str] = Field(default=None, max_length=50)
    observed_at: Optional[datetime] = None
    buckets: List[UsageBucket] = Field(max_length=20)


class UsageReport(BaseModel):
    providers: List[ProviderUsage] = Field(default_factory=list, max_length=10)
