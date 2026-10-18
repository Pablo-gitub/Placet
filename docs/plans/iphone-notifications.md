# iPhone notifications: investigation and delivery plan

Status: reported broken on the maintainer's iPhone; diagnosis pending.

## Current implementation

The backend publishes notifications through ntfy (`backend/app/notifier.py`).
Publishing is enabled only when the server URL, topic and PWA base URL are
configured. A successful publish does not demonstrate delivery to a phone.
The frontend has a web manifest but no Web Push service worker, subscription
registration or application-icon badge integration. In-page counters are not
iOS icon badges. Reopening or reinstalling the PWA does not add that support.

## Investigation

1. Check whether ntfy is configured without displaying tokens; verify delivery
   results and whether a phone client is subscribed. Use an explicitly requested
   test notification, not an agent approval or a real collaboration request.
2. Verify current official Apple/WebKit and ntfy guidance for background push,
   self-hosted iOS delivery, Home Screen installation, permissions and badges.
   Do not assume a tailnet-only server can wake iOS in the background.
3. On the actual phone, inspect notification permissions, Focus/silent settings,
   foreground/background delivery and network reachability. Sound depends on
   system policy and cannot be guaranteed by an application setting.
4. Choose explicitly between an ntfy-client delivery path and native PWA Web
   Push. Document any external push relay, payload exposure and operational
   dependencies before enabling it.

## Acceptance criteria

- A human enables notifications through a clear permission flow; denied,
  unavailable and unsubscribed states are visible and recoverable.
- A question produces a notification when the PWA is closed or the phone is
  locked; tapping it opens the intended session/request after authentication.
- Questions and quiet turn-end notices preserve the existing priority policy.
- Pending human decisions drive the icon badge where supported; counts clear
  after decisions and reconcile on reconnect, without duplicate alerts.
- No secrets or full conversation contents enter push payloads by default.
- Verify on a physical iPhone, including expired subscriptions, backend restart,
  missing connectivity and opening from the standalone PWA.

This work is a separate tracked block alongside reserved commands; it does not
require privileged command execution or a passkey administrator service.
