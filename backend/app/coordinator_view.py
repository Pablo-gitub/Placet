"""
RFC-17: the coordinator's projection of a project.

A dedicated, closed shape: what the human wrote (names, public roles, relation types) labelled as data, and
state (nodes, relations, open requests) as metadata. Never a prompt, a reply, a project context or summary, private
node instructions, a transcript, a token, an attachment, another project's name or `shared_with`.
"""
import base64
import binascii
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.app.models import RequestModel, RequestStatus

STALE_AFTER = timedelta(minutes=30)
MAX_CHAIN = 50
DEFAULT_LIMIT, MAX_LIMIT = 50, 200


class BadCursor(ValueError):
    pass


def labelled(text: Optional[str]) -> Optional[Dict[str, str]]:
    """Text a human wrote: handed over as data with its origin, never as an instruction."""
    return None if text is None else {"origin": "human", "text": text}


def encode_cursor(request_id: int) -> str:
    return base64.urlsafe_b64encode(f"r:{request_id}".encode()).decode()


def decode_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        kind, _, value = base64.urlsafe_b64decode(cursor.encode()).decode().partition(":")
        if kind != "r" or not value.isdigit():
            raise ValueError
        return int(value)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise BadCursor("The cursor is not valid.")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class Projection:
    def __init__(self, db, project_id: int, now: Optional[datetime] = None):
        self.db, self.project_id = db, project_id
        self.now = now or datetime.now(timezone.utc)
        self.project = db.get_project(project_id)
        self.members = {n.id: n for n in self.project.nodes}
        self.shared = db.node_project_counts()

    # -- nodes ------------------------------------------------------------------------------------

    def node(self, node) -> Dict[str, Any]:
        session = self.db.get_session(node.session_id) if node.session_id else None
        observed = session.last_seen_at if session else None
        if observed is not None and observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        stale = observed is None or self.now - observed > STALE_AFTER
        if session is None:
            reachability, activity = "unknown", {"state": "unknown"}
        else:
            reachability = "reachable" if session.reachable else "unreachable"
            if session.ended_at is not None:
                activity = {"state": "unknown"}
            else:
                activity = {"state": session.state if session.state in ("busy", "idle") else "unknown"}
            if stale:
                activity = {"state": "unknown", "reason": "stale"}
        if self.shared.get(node.id, 0) > 1:
            # The node also works for another project: what it does now may be outside this one.
            activity = {"state": "unavailable", "reason": "shared_node"}
        return {
            "id": node.id, "name": labelled(node.name), "provider": node.provider,
            "public_role": labelled(node.public_role),
            "reachability": reachability, "activity": activity,
            "suspended": bool(session and session.suspended_at is not None) if session else None,
            "observed_at": _iso(observed), "stale": stale if session else True,
        }

    def nodes(self) -> List[Dict[str, Any]]:
        return [self.node(n) for n in self.project.nodes]

    def relations(self) -> List[Dict[str, Any]]:
        out = []
        for r in self.project.relations:
            if r.from_node not in self.members or r.to_node not in self.members:
                continue
            out.append({
                "id": r.id,
                "from_node": {"id": r.from_node, "name": labelled(r.from_name)},
                "to_node": {"id": r.to_node, "name": labelled(r.to_name)},
                "type": {"name": labelled(r.type_name), "description": labelled(r.type_description)},
                "approval": r.approval, "reply_approval": r.reply_approval,
            })
        return out

    # -- requests ---------------------------------------------------------------------------------

    def node_ref(self, session_id: Optional[str], human: bool = False) -> Dict[str, Any]:
        if human:
            return {"id": None, "name": None, "in_project": False, "human": True}
        session = self.db.get_session(session_id) if session_id else None
        node = self.members.get(session.node_id) if session and session.node_id else None
        if node is None:
            return {"id": None, "name": None, "in_project": False}
        return {"id": node.id, "name": labelled(node.name), "in_project": True}

    def chain_root(self, req: RequestModel) -> int:
        current, seen = req, 0
        while current.parent_request_id is not None and seen < MAX_CHAIN:
            parent = self.db.get_request(current.parent_request_id)
            if parent is None:
                break
            current, seen = parent, seen + 1
        return current.id

    @staticmethod
    def approval_state(req: RequestModel) -> str:
        return {RequestStatus.PENDING: "pending", RequestStatus.REJECTED: "rejected",
                RequestStatus.CANCELLED: "cancelled", RequestStatus.EXPIRED: "expired"}.get(req.status, "approved")

    def blocking(self, req: RequestModel) -> List[str]:
        """Why it is not moving, as closed codes."""
        codes = []
        recipient = self.db.get_session(req.recipient_session_id) if req.recipient_session_id else None
        if req.status == RequestStatus.PENDING:
            codes.append("awaiting_approval")
        elif req.status == RequestStatus.APPROVED:
            if req.scheduled_at is not None and req.scheduled_at.astimezone(timezone.utc) > self.now:
                codes.append("scheduled")
            if recipient is not None and not recipient.reachable:
                codes.append("recipient_unreachable")
            elif recipient is not None and recipient.state == "busy":
                codes.append("recipient_busy")
        elif req.status == RequestStatus.DELIVERING and req.delivery == "uncertain":
            codes.append("delivery_uncertain")
        elif req.status == RequestStatus.EXECUTING:
            if req.start_unconfirmed_at is not None:
                codes.append("start_unconfirmed")
            if req.hold_alerted_at is not None:
                codes.append("holding_queue")
        elif req.status == RequestStatus.COMPLETED:
            codes.append("awaiting_return_approval")
        return codes

    def request(self, req: RequestModel) -> Dict[str, Any]:
        return {
            "id": req.id,
            "from_node": self.node_ref(req.sender_session_id),
            "to_node": self.node_ref(req.recipient_session_id, human=req.recipient_id == "human"),
            "relation": ({"id": req.relation_id, "type": labelled(req.relation_type_name)} if req.relation_id else None),
            "parent_request_id": req.parent_request_id, "chain_root_id": self.chain_root(req), "chain_hops": req.chain_hops,
            "status": req.status.value,
            "states": {
                "approval": {"state": self.approval_state(req), "mode": req.approval, "at": _iso(req.decided_at)},
                "delivery": {"state": req.delivery, "at": _iso(req.delivery_at)},
                "execution": {"state": req.execution, "at": _iso(req.execution_at)},
                "reply": {"state": req.reply, "at": _iso(req.completed_at) if req.reply != "none" else None},
            },
            "created_at": _iso(req.created_at), "updated_at": _iso(req.updated_at), "scheduled_at": _iso(req.scheduled_at),
            "blocking": self.blocking(req),
        }


def build(db, project_id: int, limit: int, cursor: Optional[str]) -> Dict[str, Any]:
    after = decode_cursor(cursor)
    view = Projection(db, project_id)
    rows = db.project_open_requests(project_id, after, limit + 1)
    page, more = rows[:limit], len(rows) > limit
    out: Dict[str, Any] = {
        "project": {"id": project_id, "name": labelled(view.project.name)},
        "generated_at": view.now.isoformat(),
        "requests": [view.request(r) for r in page],
        "next_cursor": encode_cursor(page[-1].id) if more and page else None,
    }
    if not cursor:                                  # the shape of the project comes with the first page only
        out["nodes"], out["relations"] = view.nodes(), view.relations()
    else:
        out["nodes"], out["relations"] = [], []
    return out
