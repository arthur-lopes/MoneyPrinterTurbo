#!/usr/bin/env bash
set -euo pipefail

[[ $(id -u) = 0 && $# = 1 && $(uname -m) = x86_64 ]]
scripts=$(cd -- "$(dirname -- "$0")" && pwd)
key=$(<"$1")
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [A-Za-z0-9._-]+)?$ ]]
ssh-keygen -lf "$1" >/dev/null

getent passwd moneyprinter >/dev/null ||
    useradd --system --user-group --home-dir /var/lib/moneyprinter --shell /usr/sbin/nologin moneyprinter
getent passwd moneyprinter-deploy >/dev/null ||
    useradd --system --user-group --home-dir /var/lib/moneyprinter-deploy --shell /bin/bash moneyprinter-deploy

install -d -m 755 /opt/moneyprinter/bin /usr/local/libexec /var/lib/moneyprinter-deploy/.ssh
install -d -m 755 -o moneyprinter-deploy -g moneyprinter-deploy \
    /opt/moneyprinter/deployment /opt/moneyprinter/deployment/releases /opt/moneyprinter/python /var/cache/moneyprinter-deploy
if [[ ! -f /opt/moneyprinter/deployment/drain.lock ]]; then
    install -m 644 -o moneyprinter-deploy -g moneyprinter-deploy /dev/null /opt/moneyprinter/deployment/drain.lock
fi
install -d -m 700 -o moneyprinter -g moneyprinter /var/lib/moneyprinter /var/lib/moneyprinter/codex
install -d -m 700 /etc/moneyprinter
if [[ ! -f /etc/moneyprinter/service.env ]]; then
    (umask 077; printf 'MPT_PRODUCTION_ACCESS_TOKEN=%s\n' "$(openssl rand -hex 32)" >/etc/moneyprinter/service.env)
fi

stage=$(mktemp -d)
trap 'rm -rf -- "$stage"' EXIT
download() {
    local url=$1 sha=$2 path=$3
    curl --proto '=https' --proto-redir '=https' --fail --location --silent --show-error \
        --connect-timeout 20 --max-time 240 --retry 2 --output "$path" "$url"
    printf '%s  %s\n' "$sha" "$path" | sha256sum --check --status
}

if [[ ! -x /opt/moneyprinter/bin/uv ]]; then
    download https://github.com/astral-sh/uv/releases/download/0.13.9/uv-x86_64-unknown-linux-gnu.tar.gz \
        a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5 "$stage/uv.tgz"
    tar -xzf "$stage/uv.tgz" -C "$stage" uv-x86_64-unknown-linux-gnu/uv
    install -m 755 "$stage/uv-x86_64-unknown-linux-gnu/uv" /opt/moneyprinter/bin/uv
fi
if [[ ! -x /opt/moneyprinter/codex/bin/codex ]]; then
    download https://github.com/openai/codex/releases/download/rust-v0.167.2/codex-package-x86_64-unknown-linux-musl.tar.zst \
        bbea7b9a68f450b3af18e36130ef8887e6ea4290b1cfc3096678eb6cbe9f3f34 "$stage/codex.tar.zst"
    mkdir "$stage/codex"
    tar --zstd -xf "$stage/codex.tar.zst" --no-same-owner --no-same-permissions -C "$stage/codex"
    mapfile -t binaries < <(find "$stage/codex" -maxdepth 3 -type f -path '*/bin/codex')
    [[ ${#binaries[@]} = 1 && -x "${binaries[0]}" && ! -e /opt/moneyprinter/codex ]]
    cp -a -- "$(dirname -- "$(dirname -- "${binaries[0]}")")" /opt/moneyprinter/codex
fi
if ! command -v espeak-ng >/dev/null; then
    DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get -o DPkg::Lock::Timeout=90 \
        install -y --no-install-recommends --no-remove --no-upgrade espeak-ng
fi

install -m 755 "$scripts/run-production.sh" /opt/moneyprinter/bin/run-production
install -m 755 "$scripts/receive-release.sh" /usr/local/libexec/moneyprinter-receive-release
install -m 644 "$scripts/moneyprinter.service" /etc/systemd/system/moneyprinter.service
printf '%s\n' 'moneyprinter-deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart moneyprinter.service' >"$stage/sudoers"
visudo -cf "$stage/sudoers"
install -m 440 "$stage/sudoers" /etc/sudoers.d/moneyprinter-deploy
printf 'restrict,command="/usr/local/libexec/moneyprinter-receive-release" %s\n' "$key" >"$stage/authorized_keys"
install -m 644 "$stage/authorized_keys" /var/lib/moneyprinter-deploy/.ssh/authorized_keys
systemd-analyze verify /etc/systemd/system/moneyprinter.service
systemctl daemon-reload
systemctl enable moneyprinter.service
/opt/moneyprinter/bin/uv --version
/opt/moneyprinter/codex/bin/codex --version
