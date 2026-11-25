#!/bin/sh
# Clone https://github.com/torbenwendt/ptouch-print into the home folder and
# compile it. Running it again pulls the latest commit and rebuilds.
#
# Usage: ./build-ptouch-print.sh [--skip-system-packages] [--install]
#
# --install runs "sudo make install" afterwards so ptouch-print is on PATH.
# Set PTOUCH_PRINT_DIR to clone somewhere other than ~/ptouch-print.
set -eu

repository="https://github.com/torbenwendt/ptouch-print.git"
directory="${PTOUCH_PRINT_DIR:-$HOME/ptouch-print}"
install_packages=yes
install_binary=no

for argument in "$@"; do
    case "$argument" in
        --skip-system-packages) install_packages=no ;;
        --install) install_binary=yes ;;
        -h|--help)
            sed -n '2,8s/^# \{0,1\}//p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $argument" >&2
            exit 2
            ;;
    esac
done

# Build prerequisites on Debian, Ubuntu, and Raspberry Pi OS.
packages="git build-essential autoconf automake autopoint gettext pkg-config libgd-dev libusb-1.0-0-dev"

sudo_command=""
[ "$(id -u)" -eq 0 ] || sudo_command=sudo

if [ "$install_packages" = yes ] && command -v apt-get >/dev/null 2>&1; then
    missing=""
    for package in $packages; do
        if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
            missing="$missing $package"
        fi
    done
    if [ -n "$missing" ]; then
        echo "Installing build prerequisites:$missing"
        $sudo_command apt-get update
        # shellcheck disable=SC2086
        $sudo_command apt-get install -y $missing
    fi
elif [ "$install_packages" = yes ]; then
    echo "apt-get not found; make sure these (or your distribution's equivalents) are installed:" >&2
    echo "  $packages" >&2
fi

if [ -d "$directory/.git" ]; then
    echo "Updating $directory"
    git -C "$directory" pull --ff-only
elif [ -e "$directory" ]; then
    echo "$directory exists and is not a Git checkout; move it or set PTOUCH_PRINT_DIR." >&2
    exit 1
else
    echo "Cloning $repository into $directory"
    git clone "$repository" "$directory"
fi

cd "$directory"
directory=$(pwd)
./autogen.sh
./configure --prefix=/usr
make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"

binary="$directory/ptouch-print"
if [ ! -x "$binary" ]; then
    echo "Build finished but $binary was not found." >&2
    exit 1
fi

echo
echo "Built $binary"
if [ "$install_binary" = yes ]; then
    $sudo_command make install
    echo "Installed ptouch-print to /usr/bin."
    exit 0
fi
echo "Launch P-Touch UI with it:"
echo "  PTOUCH_PRINT=$binary ~/.local/bin/ptouch-ui"
echo "Or install it system-wide:"
echo "  sudo make -C $directory install"
