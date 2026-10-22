#!/usr/bin/env python3
"""
Antigravity hook for Placet (Antigravity 2.0 app and the `agy` CLI), in
`.agents/hooks.json` of a project or `~/.gemini/config/hooks.json`.

Antigravity names the event only in the configuration, so each entry passes
it: `python3 antigravity.py --agent gemini --event Stop`.

- SessionStart: registers the conversation (project, model).
- PreInvocation, first of a turn (invocationNum 0): a turn starts (busy).
  If the user message that started it carries a Placet request
  ("[Placet Request #N"), it is acknowledged.
- Stop: the turn ended (idle). Its payload has no text, so the last answer
  is read from the conversation file and becomes the implicit reply.
- Pauses (RFC-16): Antigravity also ends a turn while a background task runs
  (the app tells the model to report and stop) or after writing a plan for the
  user to approve in the app. Such a Stop carries `pause`, and the next turn,
  started by the task's completion message or by the approval, `resumes`.

Hooks are synchronous and Antigravity expects a JSON object on stdout: this
script always prints "{}" and exits 0, so a Placet problem never blocks
the agent. Events go to the local queue (spool.py), forwarded by the bridge.
A `.placet-off` file in the project keeps it out of Placet.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import placet_common  # noqa: E402
import spool  # noqa: E402

PLACET_REQUEST = re.compile(r"\[Placet Request #(\d+)\b")
MAX_MESSAGE_CHARS = 20000
ADAPTER = "antigravity.py/3"
TAIL_BYTES = 4 * 1024 * 1024  # how far back to look for the turn's steps
# At Stop the last answer may not be in the file yet: look again briefly.
ANSWER_RETRIES = 5
ANSWER_RETRY_SECONDS = 0.4


def _number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def last_steps(path: str):
    """The steps at the end of the conversation file, newest first."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - TAIL_BYTES)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # the first line is cut
    steps = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            step = json.loads(line)
        except ValueError:
            continue
        if isinstance(step, dict):
            steps.append(step)
    return steps


BACKGROUND_STARTED = re.compile(r"Tool is running as a background task with task id: (\S+)")
BACKGROUND_FINISHED = re.compile(r"sender=(\S+) [^\n]*content=Task id \"([^\"]+)\" finished")
PLAN_CREATED = re.compile(r"Created file file://\S*/implementation_plan\.md\b")
PLAN_APPROVED = re.compile(r"^Comments on artifact URI: \S*/implementation_plan\.md\b[\s\S]*has approved")


def _content(step) -> str:
    content = step.get("content")
    return content if isinstance(content, str) else ""


def _is_resume_input(step) -> bool:
    """The input that starts a turn continuing earlier work, not a new request."""
    text = _content(step)
    if step.get("type") == "SYSTEM_MESSAGE":
        return bool(BACKGROUND_FINISHED.search(text))
    return step.get("type") == "USER_INPUT" and bool(PLAN_APPROVED.search(text.strip()))


def turn_pause(path: str):
    """Why this Stop is a pause and not the end of the work, or None."""
    steps = last_steps(path)
    started, finished = set(), set()
    for step in steps:  # newest first, back to the user message of the task
        if step.get("type") == "USER_INPUT" and not _is_resume_input(step):
            break
        text = _content(step)
        if step.get("type") == "GENERIC":
            started.update(BACKGROUND_STARTED.findall(text))
        elif step.get("type") == "SYSTEM_MESSAGE":
            finished.update(task for _, task in BACKGROUND_FINISHED.findall(text))
    if started - finished:
        return "background_task"
    # A plan written as the last action of the turn waits for the app's review.
    for step in steps:
        if step.get("type") == "PLANNER_RESPONSE":
            continue
        if step.get("type") == "GENERIC" and PLAN_CREATED.search(_content(step)):
            return "app_review"
        return None
    return None


def resumes(path: str) -> bool:
    """Whether the turn starting now continues the paused one."""
    for step in last_steps(path):
        if step.get("type") in ("USER_INPUT", "SYSTEM_MESSAGE"):
            return _is_resume_input(step)
    return False


def turn_request_id(path: str):
    """The Placet request in the user message that started the current turn."""
    for step in last_steps(path):
        if step.get("type") == "USER_INPUT":
            match = PLACET_REQUEST.search(step.get("content") or "")
            return int(match.group(1)) if match else None
    return None


def last_answer(path: str):
    """The last text the model wrote in the current turn."""
    for step in last_steps(path):
        if step.get("type") == "USER_INPUT":
            return None
        if step.get("type") == "PLANNER_RESPONSE" and (step.get("content") or "").strip():
            return step["content"][:MAX_MESSAGE_CHARS]
    return None


def build_event(event_name: str, payload: dict):
    """The /api/sessions/{id}/events body for this hook call, or None."""
    workspaces = payload.get("workspacePaths") or []
    common = {
        "provider": "antigravity",
        "entrypoint": "antigravity",
        "cwd": placet_common.project_root(workspaces[0]) if workspaces else None,
        "adapter": ADAPTER,
        # The model in use (RF-61), e.g. "gemini-3.8-flash-medium".
        "model": payload.get("modelName") if isinstance(payload.get("modelName"), str) else None,
    }
    transcript = payload.get("transcriptPath") or ""
    if event_name == "SessionStart":
        return {"event": "start", **common}
    if event_name == "PreInvocation":
        if _number(payload.get("invocationNum")) != 0:
            return None  # a later model call of the same turn
        body = {"event": "prompt", **common}
        if resumes(transcript):
            body["resumes"] = True
            return body
        request_id = turn_request_id(transcript)
        if request_id is not None:
            body["request_id"] = request_id
        return body
    if event_name == "Stop":
        body = {"event": "stop", **common}
        answer = last_answer(transcript)
        for _ in range(ANSWER_RETRIES if transcript and os.path.exists(transcript) else 0):
            if answer:
                break
            time.sleep(ANSWER_RETRY_SECONDS)
            answer = last_answer(transcript)
        if answer:
            body["last_message"] = answer
        pause = turn_pause(transcript)
        if pause:
            body["pause"] = pause
        return body
    return None


def main() -> None:
    agent = placet_common.agent_id(sys.argv)
    event_name = placet_common.option(sys.argv, "--event") or ""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if not agent or not isinstance(payload, dict):
        return
    session_id = payload.get("conversationId")
    body = build_event(event_name, payload)
    if not session_id or body is None or not placet_common.active(body.get("cwd")):
        return
    try:
        spool.write_event(agent, session_id, {k: v for k, v in body.items() if v is not None})
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        print("{}")
        sys.stdout.flush()
        os._exit(0)
