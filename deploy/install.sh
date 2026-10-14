#!/usr/bin/env bash
# Installs or updates Placet on this machine:
#   - backend as the "placet" system user (systemd: placet-backend.service)
#   - bridge as your user (systemd --user: placet-bridge.service)
#
# Run from the repository root:   sudo deploy/install.sh [--login you@github]
#
# Re-running it updates the code in /opt/placet and restarts the services;
# configuration in /etc/placet and ~/.config/placet is never overwritten.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/placet
ETC_DIR=/etc/placet
SERVICE_USER=placet
LOGIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --login) LOGIN="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0 $*" >&2
  exit 1
fi
OWNER="${SUDO_USER:-}"
if [[ -z "$OWNER" || "$OWNER" == root ]]; then
  echo "Run through sudo from the user that owns the agents' tmux sessions." >&2
  exit 1
fi
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
OWNER_UID="$(id -u "$OWNER")"

as_owner() { sudo -u "$OWNER" -H "$@"; }
step() { printf '\n==> %s\n' "$*"; }
token() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }

step "Building the PWA (as $OWNER)"
# sudo resets PATH: load nvm if node comes from there.
as_owner bash -c "
  export NVM_DIR=\"\$HOME/.nvm\"
  [ -s \"\$NVM_DIR/nvm.sh\" ] && . \"\$NVM_DIR/nvm.sh\"
  command -v npm >/dev/null || { echo 'npm not found for $OWNER' >&2; exit 1; }
  cd '$REPO_DIR/frontend' && npm ci --no-audit --no-fund && npm run build
"

step "System user $SERVICE_USER"
if ! id "$SERVICE_USER" &>/dev/null; then
  useradd --system --home-dir /var/lib/placet --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

step "Code in $APP_DIR (owned by root, read-only for everyone else)"
install -d -m 0755 -o root -g root "$APP_DIR"
rsync -a --delete \
  --exclude '.git/' --exclude '.venv/' --exclude 'tests/' --exclude '.claude/' \
  --exclude 'frontend/node_modules/' --exclude 'frontend/src/' \
  --exclude '*.db' --exclude '*.db-*' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  "$REPO_DIR/" "$APP_DIR/"
chown -R root:root "$APP_DIR"
chmod -R go-w "$APP_DIR"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

step "Configuration in $ETC_DIR"
install -d -m 0750 -o root -g "$SERVICE_USER" "$ETC_DIR"
if [[ ! -f "$ETC_DIR/AGENTS.md" ]]; then
  install -m 0640 -o root -g "$SERVICE_USER" "$REPO_DIR/AGENTS.md" "$ETC_DIR/AGENTS.md"
  echo "Created $ETC_DIR/AGENTS.md (edit it there: the repository copy is not used)."
fi

ENV_FILE="$ETC_DIR/placet.env"
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -z "$LOGIN" ]] && command -v tailscale &>/dev/null; then
    LOGIN="$(tailscale status --json 2>/dev/null | python3 -c '
import json, sys
s = json.load(sys.stdin)
print(s.get("User", {}).get(str(s.get("Self", {}).get("UserID")), {}).get("LoginName", ""))
' || true)"
  fi
  if [[ -z "$LOGIN" ]]; then
    echo "Cannot detect your Tailscale login: pass --login you@github." >&2
    exit 1
  fi
  umask 027
  cat > "$ENV_FILE" <<EOF
# Placet backend configuration (read by placet-backend.service).
PLACET_ALLOWED_USERS=$LOGIN
PLACET_PORT=8000

# Agent tokens, referenced as \${PLACET_TOKEN_*} in $ETC_DIR/AGENTS.md.
PLACET_TOKEN_CLAUDE=$(token)
PLACET_TOKEN_GEMINI=$(token)
PLACET_TOKEN_CODEX=$(token)
PLACET_BRIDGE_TOKEN=$(token)

# Notifications (see README): uncomment once ntfy runs.
#PLACET_BASE_URL=https://$(hostname).<tailnet>.ts.net
#PLACET_NTFY_URL=https://$(hostname).<tailnet>.ts.net:8443
#PLACET_NTFY_TOPIC=placet
#PLACET_NTFY_TOKEN=
EOF
  chown root:"$SERVICE_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
  echo "Created $ENV_FILE for Tailscale user $LOGIN."
fi

# Secrets the owner's processes need: the bridge token and the agent tokens
# for the MCP configurations.
OWNER_CONF="$OWNER_HOME/.config/placet"
as_owner install -d -m 0700 "$OWNER_CONF"
get_env() { grep -E "^$1=" "$ENV_FILE" | cut -d= -f2-; }
if [[ ! -f "$OWNER_CONF/bridge.env" ]]; then
  as_owner bash -c "umask 077; cat > '$OWNER_CONF/bridge.env'" <<EOF
PLACET_API_URL=http://127.0.0.1:$(get_env PLACET_PORT)
PLACET_BRIDGE_TOKEN=$(get_env PLACET_BRIDGE_TOKEN)
EOF
fi
if [[ ! -f "$OWNER_CONF/agent-tokens.env" ]]; then
  as_owner bash -c "umask 077; cat > '$OWNER_CONF/agent-tokens.env'" <<EOF
# Use these as PLACET_API_TOKEN in each agent's MCP configuration.
PLACET_TOKEN_CLAUDE=$(get_env PLACET_TOKEN_CLAUDE)
PLACET_TOKEN_GEMINI=$(get_env PLACET_TOKEN_GEMINI)
PLACET_TOKEN_CODEX=$(get_env PLACET_TOKEN_CODEX)
EOF
fi

step "Backend service"
install -m 0644 "$REPO_DIR/deploy/placet-backend.service" /etc/systemd/system/placet-backend.service
systemctl daemon-reload
systemctl enable placet-backend.service
systemctl restart placet-backend.service

step "Bridge service (user $OWNER)"
as_owner install -d "$OWNER_HOME/.config/systemd/user"
as_owner install -m 0644 "$REPO_DIR/deploy/placet-bridge.service" "$OWNER_HOME/.config/systemd/user/placet-bridge.service"
user_systemctl() { as_owner env XDG_RUNTIME_DIR="/run/user/$OWNER_UID" systemctl --user "$@"; }
if user_systemctl daemon-reload; then
  user_systemctl enable placet-bridge.service
  user_systemctl restart placet-bridge.service
else
  echo "Could not reach your user systemd; after logging in run:"
  echo "  systemctl --user daemon-reload && systemctl --user enable --now placet-bridge"
fi

step "Done"
cat <<EOF
Backend:  systemctl status placet-backend    (logs: journalctl -u placet-backend -f)
Bridge:   systemctl --user status placet-bridge
Tokens for the agents' MCP configs: $OWNER_CONF/agent-tokens.env

To reach the PWA from the iPhone (once):
  sudo tailscale serve --bg unix:/run/placet/human.sock
To keep the bridge running when you are logged out (optional):
  sudo loginctl enable-linger $OWNER
Edit agents and routing rules in $ETC_DIR/AGENTS.md, then:
  sudo systemctl restart placet-backend
EOF
