#!/usr/bin/env bash
# Install powermon as a systemd user service on this Linux machine.
#
#   ./install.sh            reachable from other devices, protected by a token
#   ./install.sh --local    this machine only (127.0.0.1, no token)
#   ./install.sh --rapl     also install the udev rule for measured CPU power (sudo)
#
# Or without cloning first:
#   curl -fsSL https://raw.githubusercontent.com/marzmesas/powermon/main/install.sh | bash
#
# Safe to run again: an existing config.toml is never modified.
set -euo pipefail

REPO=https://github.com/marzmesas/powermon.git
LOCAL=0 RAPL=0
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --rapl) RAPL=1 ;;
        *) echo "usage: install.sh [--local] [--rapl]" >&2; exit 2 ;;
    esac
done

die() { echo "install: $*" >&2; exit 1; }

[ "$(uname -s)" = Linux ] || die "powermon runs on Linux only (the Mac app is a client of it)"
command -v python3 >/dev/null || die "python3 not found"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || die "python 3.10 or newer is required"
systemctl --user show-environment >/dev/null 2>&1 \
    || die "no systemd user session; log in as a normal user (not sudo/su) and retry"

# Use the checkout this script sits in; piped from curl there is none, so clone.
DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-.}")" && pwd)
if [ ! -f "$DIR/powermon.py" ]; then
    DIR=${POWERMON_DIR:-$HOME/powermon}
    [ -f "$DIR/powermon.py" ] || git clone --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"

if [ -f config.toml ]; then
    echo "Keeping the existing config.toml"
else
    cp config.example.toml config.toml
    chmod 600 config.toml   # it holds the token
    if [ "$LOCAL" = 0 ]; then
        token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
        # First match only: [server] is the first section of the example.
        sed -i -e '0,/^host = .*/s//host = "0.0.0.0"/' \
               -e "0,/^token = .*/s//token = \"$token\"/" config.toml
    fi
fi

mkdir -p ~/.config/systemd/user ~/.local/bin
sed "s|/home/marzmesas/powermon|$DIR|g" systemd/powermon.service \
    > ~/.config/systemd/user/powermon.service
ln -sf "$DIR/pwr" ~/.local/bin/pwr

if [ "$RAPL" = 1 ]; then
    sudo cp 90-rapl-readable.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=powercap
fi

systemctl --user daemon-reload
systemctl --user enable powermon >/dev/null 2>&1
systemctl --user restart powermon

# Without linger the service stops at logout and does not start at boot.
loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER" \
    || echo "install: could not enable linger; run: sudo loginctl enable-linger $USER" >&2

# Read back what the service will actually use, so a kept config prints right.
read -r host port token < <(python3 - <<'EOF'
import powermon
s = powermon.load_config(powermon.HERE / "config.toml")["server"]
print(s["host"], s["port"], s.get("token") or "-")
EOF
)

up=0
for _ in $(seq 15); do
    if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/api/now', timeout=2)" 2>/dev/null; then
        up=1; break
    fi
    sleep 1
done
[ "$up" = 1 ] || die "the service did not answer on port $port; see: journalctl --user -u powermon -n 30"

echo
echo "powermon is running."
echo "  On this machine:  http://localhost:$port   (or run: pwr)"
if [ "$host" != 127.0.0.1 ] && [ "$token" != - ]; then
    addr=$(tailscale ip -4 2>/dev/null | head -n1 || true)
    [ -n "$addr" ] || addr=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo "  From elsewhere:   http://${addr:-<this-machine>}:$port/?token=$token"
    echo "  Mac menu bar app: Server URL http://${addr:-<this-machine>}:$port, Token $token"
else
    echo "  From elsewhere:   ssh -L $port:localhost:$port $USER@$(hostname), then http://localhost:$port"
fi
echo "Set your electricity price in $DIR/config.toml under [tariff], then:"
echo "  systemctl --user restart powermon"
cat /sys/class/powercap/*rapl*/energy_uj >/dev/null 2>&1 \
    || echo "CPU power is estimated until you rerun with --rapl (needs sudo)."
