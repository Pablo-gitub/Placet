"""
Shows how the bridge judges a tmux session, to tune idle_patterns and
busy_patterns in AGENTS.md against the real agent TUI.

    python -m bridge.calibrate <agent-id>            # one check
    python -m bridge.calibrate <agent-id> --watch    # repeat every 2s

Try it with the agent idle at an empty prompt, while it is working, while
you are typing, and with a menu or dialog open: only the first must say
IDLE.
"""
import argparse
import time

from backend.app.config import load_agents_config
from bridge.tmux_controller import TmuxController


def report(tmux: TmuxController, session: str, idle_patterns, busy_patterns) -> None:
    check = tmux.check_idle(session, idle_patterns, busy_patterns)
    lines = [line.rstrip() for line in check.screen.splitlines() if line.strip()]
    tail = lines[-TmuxController.IDLE_TAIL_LINES:]
    print(f"--- last {len(tail)} non-empty lines from {session}:")
    for line in tail:
        mark = "=>" if line == check.matched_line else "  "
        print(f"{mark} {line}")
    print(f"--- verdict: {'IDLE' if check.idle else 'BUSY'} ({check.reason})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("agent", help="agent id in AGENTS.md (e.g. claude)")
    parser.add_argument("--watch", action="store_true", help="repeat every 2 seconds")
    parser.add_argument("--agents-file", help="path to AGENTS.md")
    args = parser.parse_args()

    spec = load_agents_config(args.agents_file).get_agent(args.agent)
    if spec is None:
        raise SystemExit(f"Agent {args.agent!r} is not in AGENTS.md.")
    tmux = TmuxController()
    if not tmux.session_exists(spec.tmux_session):
        raise SystemExit(f"tmux session {spec.tmux_session!r} is not running.")

    print(f"idle_patterns: {spec.idle_patterns or '(shell prompt)'}")
    print(f"additional busy_patterns: {spec.busy_patterns or '-'}")
    while True:
        report(tmux, spec.tmux_session, spec.idle_patterns, spec.busy_patterns)
        if not args.watch:
            break
        time.sleep(2)
        print()


if __name__ == "__main__":
    main()
