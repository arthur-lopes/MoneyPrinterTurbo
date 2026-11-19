#!/usr/bin/env bash
set -euo pipefail
umask 022

deployment=/opt/moneyprinter/deployment
health_url=http://127.0.0.1:8765/api/health
request=${SSH_ORIGINAL_COMMAND:-}
if [[ "$request" = status ]]; then
    exec curl --fail --silent --show-error --max-time 5 "$health_url"
fi
if ! [[ "$request" =~ ^deploy\ ([0-9a-f]{40})$ ]]; then
    printf '%s\n' 'This key is restricted to MoneyPrinter releases.' >&2
    exit 64
fi
revision=${BASH_REMATCH[1]}
release="$deployment/releases/$revision"

exec 9>"$deployment/deploy.lock"
flock --timeout 900 9

health_matches() {
    curl --fail --silent --max-time 3 "$health_url" | /usr/bin/python3 -c '
import json, sys
state = json.load(sys.stdin)
sys.exit(not (state["status"] == "ready" and state["revision"] == sys.argv[1]))
' "$1" 2>/dev/null
}

await_health() {
    local attempt
    for attempt in {1..30}; do
        if health_matches "$1"; then
            return 0
        fi
        sleep 2
    done
    return 1
}

switch_link() {
    local path=$1 name=$2
    local link="$deployment/.$name-$$"
    ln -s -- "$path" "$link"
    mv -Tf -- "$link" "$deployment/$name"
}

current=$(readlink -f -- "$deployment/current" || :)
rollback=$(readlink -f -- "$deployment/last-good" || :)
if [[ "$current" = "$release" ]] && health_matches "$revision"; then
    cat >/dev/null
    printf 'Already serving %s\n' "$revision"
    exit 0
fi

if [[ ! -f "$release/READY" ]]; then
    [[ "$current" != "$release" ]]
    if [[ -e "$release" ]]; then
        [[ -d "$release" && ! -L "$release" ]]
        rm -rf -- "$release"
    fi
    mkdir -- "$release"
    trap 'if [[ ! -f "$release/READY" ]]; then rm -rf -- "$release"; fi' EXIT
    wheel=$(/usr/bin/python3 - "$release" "$revision" 3<&0 <<'PY'
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

destination = Path(sys.argv[1])
limits = {"pyproject.toml": 512 * 1024, "uv.lock": 8 * 1024**2, "REVISION": 41}
seen = set()
wheel = None
with os.fdopen(3, "rb") as incoming, tarfile.open(fileobj=incoming, mode="r|gz") as bundle:
    for member in bundle:
        name = member.name
        if name not in limits:
            if wheel or not re.fullmatch(r"mpt-[a-zA-Z0-9_.+]+-py3-none-any[.]whl", name):
                raise SystemExit("Invalid release bundle member")
            wheel = name
        size_limit = limits.get(name, 256 * 1024**2)
        if not member.isfile() or name in seen or not 0 < member.size <= size_limit:
            raise SystemExit("Invalid release bundle member or size")
        with bundle.extractfile(member) as source, (destination / name).open("xb") as output:
            shutil.copyfileobj(source, output)
        seen.add(name)
if not wheel or not limits.keys() <= seen or (destination / "REVISION").read_text().strip() != sys.argv[2]:
    raise SystemExit("Missing release files or revision mismatch")
print(wheel)
PY
    )
    export UV_CACHE_DIR=/var/cache/moneyprinter-deploy/uv
    export UV_PYTHON_INSTALL_DIR=/opt/moneyprinter/python
    export UV_NO_PROGRESS=1
    uv=/opt/moneyprinter/bin/uv
    "$uv" sync --project "$release" --frozen --no-dev --no-install-project --python 3.11.18 --managed-python
    "$uv" pip install --python "$release/.venv/bin/python" --no-deps "$release/$wheel"
    "$release/.venv/bin/mpt" production --help >/dev/null
    rm -- "$release/$wheel"
    touch -- "$release/READY"
    trap - EXIT
else
    cat >/dev/null
fi

exec 8<>"$deployment/drain.lock"
flock --timeout 10 8
if systemctl is-active --quiet moneyprinter.service; then
    deadline=$((SECONDS + 1920))
    while ! curl --fail --silent --max-time 3 "$health_url" | /usr/bin/python3 -c '
import json, sys
state = json.load(sys.stdin)
sys.exit(state["active_jobs"] != 0)
' 2>/dev/null; do
        if (( SECONDS >= deadline )); then
            printf '%s\n' 'Deployment timed out waiting for running jobs.' >&2
            exit 75
        fi
        sleep 3
    done
fi

switch_link "$release" current
if ! sudo -n /usr/bin/systemctl restart moneyprinter.service || ! await_health "$revision"; then
    printf 'Release %s failed its health check.\n' "$revision" >&2
    if [[ "$rollback" =~ ^/opt/moneyprinter/deployment/releases/[0-9a-f]{40}$ && -f "$rollback/READY" ]]; then
        switch_link "$rollback" current
        sudo -n /usr/bin/systemctl restart moneyprinter.service
        await_health "${rollback##*/}" || printf '%s\n' 'Rollback failed its health check.' >&2
    fi
    exit 1
fi

switch_link "$release" last-good
flock --unlock 8
printf 'Now serving %s\n' "$revision"
# Application data lives outside releases. Keep the current version and its last working predecessor.
for old in "$deployment"/releases/*; do
    if [[ "$old" != "$release" && "$old" != "$rollback" && -d "$old" && ! -L "$old" &&
          "${old##*/}" =~ ^[0-9a-f]{40}$ && -f "$old/READY" ]]; then
        rm -rf -- "$old"
    fi
done
