#!/usr/bin/env bash
set -euo pipefail

release_dir=$(readlink -f -- /opt/moneyprinter/deployment/current)
[[ "$release_dir" =~ ^/opt/moneyprinter/deployment/releases/[0-9a-f]{40}$ ]]
revision=$(<"$release_dir/REVISION")
[[ "$revision" = "${release_dir##*/}" ]]
export MPT_PRODUCTION_RELEASE="$revision"
exec "$release_dir/.venv/bin/mpt" production --port 8765 --data-dir /var/lib/moneyprinter/production
