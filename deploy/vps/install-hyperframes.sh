#!/usr/bin/env bash
set -euo pipefail

# Run as root on the Ubuntu x86_64 studio host after the VPS bootstrap.
[[ "$(id -u)" == 0 && "$(uname -m)" == x86_64 ]]
root=/opt/moneyprinter/hyperframes
if [[ -x "$root/bin/hyperframes" && -x "$root/chrome/chrome-headless-shell" ]]; then
    "$root/bin/hyperframes" --version
    exit 0
fi
[[ ! -e "$root" ]]

DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get -o DPkg::Lock::Timeout=90 \
    install -y --no-install-recommends ffmpeg libatk1.0-0 libatk-bridge2.0-0 \
    libgbm1 libpango-1.0-0 libcups2 libxcomposite1 libxdamage1 libxrandr2 \
    libxkbcommon0 fonts-liberation

stage=$(mktemp -d /opt/moneyprinter/.hyperframes.XXXXXX)
trap 'if [[ "$stage" =~ ^/opt/moneyprinter/\.hyperframes\.[A-Za-z0-9]{6}$ ]]; then rm -rf -- "$stage"; fi' EXIT
chmod 755 "$stage"
node_archive=node-v22.23.5-linux-x64.tar.xz
curl --fail --silent --show-error --retry 2 \
    "https://nodejs.org/dist/v22.23.5/$node_archive" -o "$stage/$node_archive"
printf '%s  %s\n' cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8 \
    "$stage/$node_archive" | sha256sum -c
mkdir "$stage/node" "$stage/app" "$stage/bin" "$stage/chrome" "$stage/home"
tar -xJf "$stage/$node_archive" --strip-components=1 --no-same-owner -C "$stage/node"
export PATH="$stage/node/bin:$PATH"
export HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1
npm --cache "$stage/npm-cache" --prefix "$stage/app" install --omit=dev \
    --no-audit --no-fund --ignore-scripts --save-exact hyperframes@0.9.70
cli="$stage/app/node_modules/hyperframes/bin/hyperframes.mjs"
HOME="$stage/home" node "$cli" browser ensure
browser=$(HOME="$stage/home" node "$cli" browser path | tail -n1)
[[ -x "$browser" && "$browser" == "$stage/home/"* ]]
cp -a "$(dirname "$browser")/." "$stage/chrome/"
if ldd "$stage/chrome/chrome-headless-shell" | grep -q 'not found'; then
    printf '%s\n' "A Chromium library is missing" >&2
    exit 1
fi
cat >"$stage/bin/hyperframes" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
root=/opt/moneyprinter/hyperframes
export PATH="$root/node/bin:$PATH"
export HYPERFRAMES_BROWSER_PATH="$root/chrome/chrome-headless-shell"
export HYPERFRAMES_NO_TELEMETRY=1 DO_NOT_TRACK=1
exec "$root/node/bin/node" "$root/app/node_modules/hyperframes/bin/hyperframes.mjs" "$@"
WRAPPER
chmod 755 "$stage/bin/hyperframes"
rm -rf -- "$stage/home" "$stage/npm-cache"
rm -- "$stage/$node_archive"
mv -T -- "$stage" "$root"
runuser -u moneyprinter -- "$root/bin/hyperframes" --version
