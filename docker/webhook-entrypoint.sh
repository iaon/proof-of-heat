#!/bin/sh
set -eu

ssh_dir=/root/.ssh
default_key_path=/run/webhook-ssh/id_ed25519
key_target="$ssh_dir/id_ed25519"
key_source="${WEBHOOK_SSH_PRIVATE_KEY_FILE:-$default_key_path}"

mkdir -p "$ssh_dir"
chmod 700 "$ssh_dir"

if [ -n "${WEBHOOK_SSH_PRIVATE_KEY:-}" ]; then
    printf '%s\n' "$WEBHOOK_SSH_PRIVATE_KEY" > "$key_target"
    chmod 600 "$key_target"
elif [ -f "$key_source" ]; then
    cp "$key_source" "$key_target"
    chmod 600 "$key_target"
fi

if [ -f "$key_target" ]; then
    export GIT_SSH_COMMAND="ssh -i $key_target -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
fi

exec webhook "$@"
