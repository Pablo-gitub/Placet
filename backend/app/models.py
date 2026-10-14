from enum import Enum
from typing import List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class RequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


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
    sender_id: str
    recipient_id: str
    question_type: QuestionType = QuestionType.PROMPT_REVIEW
    prompt: str
    context: Optional[str] = None
    options: List[str] = Field(default_factory=list)
    timeout_seconds: int = 180


class RequestDecision(BaseModel):
    status: RequestStatus  # usually APPROVED or REJECTED
    edited_prompt: Optional[str] = None
    selected_options: List[str] = Field(default_factory=list)
    custom_text: Optional[str] = None


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
    timeout_seconds: int = 180
    created_at: datetime
    decided_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    response: Optional[ResponseModel] = None

