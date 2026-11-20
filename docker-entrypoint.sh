#!/bin/sh
# First start: write /data/config.toml from the example, bound to all interfaces,
# with an access token. Later starts leave an existing config.toml untouched.
set -eu

CONFIG=/data/config.toml

if [ ! -e "$CONFIG" ]; then
    if [ -n "${POWERMON_TOKEN:-}" ]; then
        token="$POWERMON_TOKEN"
    else
        token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')
        echo "powermon: generated access token: $token"
    fi

    # Escape backslash and double quote so the value is a valid TOML string.
    # It travels through the environment, so awk does not reinterpret escapes.
    token_toml=$(printf '%s' "$token" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')

    # Only the first "host =" and first "token =" lines are the [server] keys
    # (the example has other token-like keys later). The db key goes under [sampling].
    POWERMON_TOKEN_TOML="$token_toml" awk '
        BEGIN { hd = 0; td = 0 }
        !hd && /^host = /  { print "host = \"0.0.0.0\""; hd = 1; next }
        !td && /^token = / { print "token = \"" ENVIRON["POWERMON_TOKEN_TOML"] "\""; td = 1; next }
        /^\[sampling\][ \t]*$/ { print; print "db = \"/data/powermon.db\""; next }
        { print }
    ' /app/config.example.toml > "$CONFIG.tmp"
    mv "$CONFIG.tmp" "$CONFIG"
fi

exec python3 /app/powermon.py /data/config.toml
