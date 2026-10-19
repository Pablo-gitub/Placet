"""Protected helper executed only AFTER dropping root and supplemental groups.

No arguments, shell, home imports, interactive session or arbitrary user command.
The user service manager itself is controlled by the desktop user; its replies
are useful status signals, never a source of privileged instructions.
"""
import os
from pathlib import Path
import sys


def main():
    if os.geteuid() == 0 or len(sys.argv) != 1:
        raise ValueError('Unprivileged fixed helper required')
    # -I excludes the checkout and user site. The installed sibling module must
    # live under the protected administrative prefix supplied by the installer.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from restart import BRIDGE, restart_service
    env = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
           'XDG_RUNTIME_DIR': f'/run/user/{os.geteuid()}',
           'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{os.geteuid()}/bus'}
    print(restart_service(BRIDGE, user=True, env=env))


if __name__ == '__main__':
    main()
