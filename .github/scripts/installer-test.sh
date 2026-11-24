#!/bin/sh
# End-to-end check of install.sh and the uninstaller on a Debian-family system.
# With --container, run as root in a fresh Debian or Ubuntu image: create a
# desktop-style user with sudo and rerun this script as that user.
set -eu

if [ "${1:-}" = "--container" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    # The README expects git and python3 to be present already.
    apt-get install -y -qq --no-install-recommends ca-certificates git python3 sudo >/dev/null
    useradd --create-home tester
    echo "tester ALL=(ALL) NOPASSWD:ALL" >/etc/sudoers.d/tester
    cp -R /src /home/tester/ptouch-ui
    chown -R tester:tester /home/tester/ptouch-ui
    exec su - tester -c "sh /home/tester/ptouch-ui/.github/scripts/installer-test.sh"
fi

cd "$(dirname "$0")/../.."
if command -v ptouch-print >/dev/null; then
    echo "ptouch-print is already installed; this test needs a system without it." >&2
    exit 1
fi

./install.sh
echo "--- installed"
ptouch-print --help >/dev/null
command -v ptouch-print
test -x "$HOME/.local/bin/ptouch-ui"
"$HOME/.local/share/ptouch-ui/current/venv/bin/python" -I -c "import ptouch_ui.app"
test -f "$HOME/.local/share/ptouch-ui/ptouch-print.json"
cat "$HOME/.local/share/ptouch-ui/ptouch-print.json"

./install.sh
echo "--- reinstalled"
test -f "$HOME/.local/share/ptouch-ui/ptouch-print.json"

"$HOME/.local/bin/ptouch-ui-uninstall"
echo "--- uninstalled"
hash -r 2>/dev/null || true
if command -v ptouch-print >/dev/null; then
    echo "ptouch-print was not removed." >&2
    exit 1
fi
test ! -e "$HOME/.local/share/ptouch-ui"
test ! -e "$HOME/.local/bin/ptouch-ui"
echo "Installer test passed."
