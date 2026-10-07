#!/usr/bin/env bash
# launch-http-grr.sh <run id>
#
# Serve a scratch copy of test_fixtures/mini-GRR as an HTTP GRR from the
# run's own compose project, verify-gain-<run id>. Run it after launch.sh.
#   1. Copy test_fixtures/mini-GRR (without its .git file) to
#      scratch/http/grr. The submodule is never written. The submodule
#      commits .CONTENTS*, so the copy is an HTTP GRR as it is.
#   2. Write scratch/http/compose.override.yaml: it mounts the copy as
#      htdocs (an absolute path) and publishes port 80 on an ephemeral
#      127.0.0.1 port.
#   3. docker compose -f docker-compose.yaml -f <override>
#      -p verify-gain-<run id> up -d --pull never apache.
#   4. Read the host port with `docker compose port apache 80` and write
#      scratch/grr-http.yaml: a `type: url` GRR with a cache_dir inside
#      scratch.
# Every compose call goes through vg_compose (common.sh). Cleanup removes
# the project with `down`; it never stops a container by name.
#
# VERIFY_GAIN_HTTPD_IMAGE=<image> replaces httpd:latest (see common.sh).

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_dir="$(vg_run_dir "${1:-}")"
scratch="$run_dir/scratch"
http_dir="$(vg_http_dir "$run_dir")"
[[ -d "$scratch" ]] || vg_die "launch-http: $scratch is missing (run launch.sh)"
[[ ! -e "$http_dir" ]] || vg_die "launch-http: $http_dir already exists; launch a new run"

# The compose project name is global to the host. Refuse a name that
# another checkout or an old run already uses, before anything is written:
# without the override file, cleanup.sh never runs `down` on that project.
vg_check_project_free "$run_dir"

mini_grr="$VG_CHECKOUT/test_fixtures/mini-GRR"
[[ -f "$mini_grr/mini_pipeline/genomic_resource.yaml" ]] \
    || vg_die "launch-http: FAIL: $mini_grr is not initialised.
    Fix: cd $VG_CHECKOUT && git submodule update --init test_fixtures/mini-GRR"

# 1. The scratch copy.
mkdir -p "$http_dir/cache"
rsync -a --exclude=/.git "$mini_grr/" "$http_dir/grr/"
[[ -f "$http_dir/grr/.CONTENTS.json" || -f "$http_dir/grr/.CONTENTS.json.gz" ]] \
    || vg_die "launch-http: FAIL: the copy $http_dir/grr has no .CONTENTS.json(.gz)"

# 2. The scratch override. Compose resolves a relative path against the
# project directory, so the bind source is absolute. A double-quoted YAML
# scalar keeps it one value; a path with '"' or '\' is refused.
grr_copy="$(cd "$http_dir/grr" && pwd -P)"
[[ "$grr_copy$VG_HTTPD_IMAGE" != *[\"\\$]* ]] \
    || vg_die "launch-http: the path $grr_copy or the image $VG_HTTPD_IMAGE holds '\"', '\\' or '\$'"
cat > "$http_dir/compose.override.yaml" <<YAML
# Written by verify-gain launch-http-grr.sh for compose project
# $(vg_compose_project "$run_dir"). Removed by cleanup.sh.
services:
  apache:
    image: "$VG_HTTPD_IMAGE"
    ports:
      - "127.0.0.1::80"
    volumes:
      - "$grr_copy:/usr/local/apache2/htdocs/:ro"
YAML

# 3. Start the run's own apache. The image must be local: no pull.
# Check the project again right before `up`: it must still be free.
vg_check_httpd_image
vg_check_project_free "$run_dir"
vg_compose "$run_dir" up -d --pull never --no-deps apache >&2 \
    || vg_die "launch-http: FAIL: docker compose up failed for $(vg_compose_project "$run_dir")"

# 4. The ephemeral host port and the HTTP GRR definition. First make sure
# that the container `up` left is this run's own.
vg_check_project_owned "$run_dir"
port_line="$(vg_compose "$run_dir" port apache 80)"
port="${port_line##*:}"
[[ "$port_line" == 127.0.0.1:* && "$port" =~ ^[0-9]+$ ]] \
    || vg_die "launch-http: FAIL: unexpected 'docker compose port apache 80' output: $port_line"
echo "$port" > "$http_dir/port"

url="http://127.0.0.1:$port/"
# Wait until httpd answers on the port (any HTTP status).
for _ in $(seq 1 40); do
    code="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "$url" || true)"
    [[ "$code" != "000" ]] && break
    sleep 0.25
done
[[ "$code" != "000" ]] || vg_die "launch-http: FAIL: $url does not answer"

sq="'"
cat > "$scratch/grr-http.yaml" <<YAML
id: mini_http
type: url
url: '$url'
cache_dir: '${http_dir//$sq/$sq$sq}/cache'
YAML

echo "launch-http: compose project $(vg_compose_project "$run_dir") serves $grr_copy at $url" >&2
echo "launch-http: wrote $scratch/grr-http.yaml" >&2
