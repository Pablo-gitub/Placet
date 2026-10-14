import os
import tempfile
import pytest
from backend.app.database import Database
from backend.app.models import RequestCreate, RequestStatus, QuestionType


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.init_db()
    yield db
    if os.path.exists(path):
        os.remove(path)


def test_create_and_get_request(temp_db):
    req_in = RequestCreate(
        sender_id="claude",
        recipient_id="human",
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="Quale approccio preferisci?",
        context="Rifattorizzazione del modulo bridge",
        options=["Opzione A", "Opzione B"],
    )
    req = temp_db.create_request(req_in)
    assert req.id is not None
    assert req.status == RequestStatus.PENDING
    assert req.sender_id == "claude"
    assert req.recipient_id == "human"
    assert req.options == ["Opzione A", "Opzione B"]
    assert req.created_at is not None

    fetched = temp_db.get_request(req.id)
    assert fetched is not None
    assert fetched.id == req.id
    assert fetched.prompt == "Quale approccio preferisci?"


def test_update_decision_approved(temp_db):
    req_in = RequestCreate(
        sender_id="claude",
        recipient_id="gemini",
        question_type=QuestionType.PROMPT_REVIEW,
        prompt="Analizza questo snippet",
        context="Verifica edge case",
    )
    req = temp_db.create_request(req_in)
    
    updated = temp_db.update_request_decision(
        request_id=req.id,
        status=RequestStatus.APPROVED,
        edited_prompt="Analizza questo snippet e verifica memoria",
    )
    assert updated.status == RequestStatus.APPROVED
    assert updated.edited_prompt == "Analizza questo snippet e verifica memoria"
    assert updated.decided_at is not None


def test_update_decision_rejected(temp_db):
    req_in = RequestCreate(
        sender_id="claude",
        recipient_id="human",
        question_type=QuestionType.CONFIRMATION,
        prompt="Posso cancellare questo file?",
    )
    req = temp_db.create_request(req_in)

    updated = temp_db.update_request_decision(
        request_id=req.id,
        status=RequestStatus.REJECTED,
        custom_text="Troppo rischioso",
    )
    assert updated.status == RequestStatus.REJECTED
    assert updated.custom_text == "Troppo rischioso"


def test_record_reply(temp_db):
    req_in = RequestCreate(
        sender_id="claude",
        recipient_id="gemini",
        question_type=QuestionType.PROMPT_REVIEW,
        prompt="Verifica algoritmo",
    )
    req = temp_db.create_request(req_in)
    temp_db.update_request_decision(req.id, RequestStatus.APPROVED)

    completed = temp_db.record_reply(
        request_id=req.id,
        responder_id="gemini",
        content="Algoritmo corretto, complessità O(n log n)",
    )
    assert completed.status == RequestStatus.COMPLETED
    assert completed.response is not None
    assert completed.response.responder_id == "gemini"
    assert "O(n log n)" in completed.response.content
    assert completed.completed_at is not None


def test_list_requests_filtering(temp_db):
    req1 = temp_db.create_request(RequestCreate(
        sender_id="claude", recipient_id="human", prompt="P1"
    ))
    req2 = temp_db.create_request(RequestCreate(
        sender_id="gemini", recipient_id="human", prompt="P2"
    ))
    temp_db.update_request_decision(req1.id, RequestStatus.APPROVED)

    pending_list = temp_db.list_requests(status=RequestStatus.PENDING)
    assert len(pending_list) == 1
    assert pending_list[0].id == req2.id

    all_list = temp_db.list_requests()
    assert len(all_list) == 2


def test_cancel_request(temp_db):
    req = temp_db.create_request(RequestCreate(
        sender_id="claude", recipient_id="human", prompt="P3"
    ))
    cancelled = temp_db.cancel_request(req.id)
    assert cancelled is True

    fetched = temp_db.get_request(req.id)
    assert fetched.status == RequestStatus.CANCELLED
