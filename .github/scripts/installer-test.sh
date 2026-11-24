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

./install.sh
echo "--- installed"
test -x "$HOME/.local/bin/ptouch-ui"
"$HOME/.local/bin/ptouch-ui" --version
test ! -e "$HOME/.local/share/ptouch-ui/ptouch-print.json"

./install.sh
echo "--- reinstalled"
"$HOME/.local/bin/ptouch-ui" --version

"$HOME/.local/bin/ptouch-ui-uninstall"
echo "--- uninstalled"
test ! -e "$HOME/.local/share/ptouch-ui"
test ! -e "$HOME/.local/bin/ptouch-ui"
test ! -e "$HOME/.local/share/applications/io.github.mrjrask.PtouchUI.desktop"
echo "Installer test passed."
