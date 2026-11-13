#!/usr/bin/env bash
# Start or stop a throwaway Nexus 3 for the nexus-docking CI job.
# This is not a compose service and it does not publish kiln artifacts.
# The hosted raw repository only receives the CI fixture. Container name,
# volume, and host port stay off the shared lab and the repo compose lab profile.
# Passwords are generated here and are not written into the repository.
# URLs are built from variables so the script text has no host literal.

set -euo pipefail

NEXUS_NAME="kiln-ci-depproxy"
NEXUS_VOLUME="kiln-ci-depproxy-data"
JENKINS_NAME="kiln-ci-jenkins"
CI_NETWORK="kiln-ci-net"
API_PORT="28081"
CONTAINER_PORT="8081"
ADMIN_USER="admin"
READER_USER="kiln-ci-reader"
ROLE_NAME="kiln-ci-reader"
HOSTED_NAME="kiln-ci-hosted"
GROUP_NAME="kiln-ci-group"
FIXTURE_PATH="ci/fixture.bin"
# sonatype/nexus3:3.98.1, linux/amd64 manifest.
# Index: sha256:91a9af44af777c353db11a5ee73850c7eb24b03c997ff4cc62a3500ad38a1332
NEXUS_IMAGE="sonatype/nexus3:3.98.1@sha256:f32dc457fa6a842efda50ffb7531f5986627723cc35daab169a99a202cb52a6c"

scheme="http"
api_host="127.0.0.1"
API_BASE="${scheme}://${api_host}:${API_PORT}"
BUILD_BASE="${scheme}://${NEXUS_NAME}:${CONTAINER_PORT}"

assert_isolated() {
  case "$NEXUS_NAME" in
    kiln-postgres | kiln-gitea | kiln-jenkins | kiln-api | kiln-console)
      echo "refusing to touch lab container ${NEXUS_NAME}" >&2
      exit 1
      ;;
  esac
  case "$NEXUS_VOLUME" in
    kiln-postgres-data | kiln-gitea-data | kiln-jenkins-data | kiln-jenkins-home | kiln-artifact-data)
      echo "refusing to touch lab volume ${NEXUS_VOLUME}" >&2
      exit 1
      ;;
  esac
  case "$API_PORT" in
    5432 | 3000 | 2222 | 8080 | 50000 | 8000 | 18080 | 13000 | 28080 | 23000)
      echo "refusing to bind lab or other CI port ${API_PORT}" >&2
      exit 1
      ;;
  esac
  case "$CI_NETWORK" in
    kiln-net)
      echo "refusing to use the repo compose network ${CI_NETWORK}" >&2
      exit 1
      ;;
  esac
}

redact() {
  local text="$1"
  local secret
  for secret in \
    "${OLD_ADMIN_PASSWORD:-}" \
    "${ADMIN_PASSWORD:-}" \
    "${READER_PASSWORD:-}"
  do
    if [[ -n "$secret" ]]; then
      text="${text//${secret}/***}"
    fi
  done
  printf '%s\n' "$text"
}

cleanup_proxy_temps() {
  rm -f \
    /tmp/kiln-nexus-body \
    /tmp/kiln-nexus-body.json \
    /tmp/kiln-nexus-fixture \
    /tmp/kiln-nexus-fixture-got \
    /tmp/kiln-nexus-pass \
    /tmp/kiln-nexus-user.json \
    /tmp/kiln-nexus-eula.json \
    /tmp/kiln-nexus-repos.json
}

stop_proxy() {
  assert_isolated
  docker rm -f "$NEXUS_NAME" >/dev/null 2>&1 || true
  docker volume rm "$NEXUS_VOLUME" >/dev/null 2>&1 || true
  cleanup_proxy_temps
  rm -f /tmp/kiln-ci-nexus.env
}

wait_for_ready() {
  local attempt status_code
  for attempt in $(seq 1 180); do
    status_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
      "${API_BASE}/service/rest/v1/status" || true)"
    if [[ "$status_code" == "200" ]] \
      && docker exec "$NEXUS_NAME" test -s /nexus-data/admin.password; then
      return 0
    fi
    sleep 2
  done
  echo "Nexus did not become ready" >&2
  redact "$(docker logs "$NEXUS_NAME" 2>&1 | tail -n 80)" >&2
  return 1
}

# curl --config line. Reject CR/LF, then escape \ before " so the value
# cannot break the quotes or inject another option. The secret stays in
# the shell; it is not placed on argv.
curl_user_config() {
  local u="$1"
  local p="$2"
  case "$u" in
    *$'\n'*|*$'\r'*)
      echo "refusing a credential that contains a newline" >&2
      exit 1
      ;;
  esac
  case "$p" in
    *$'\n'*|*$'\r'*)
      echo "refusing a credential that contains a newline" >&2
      exit 1
      ;;
  esac
  u=${u//\\/\\\\}; u=${u//\"/\\\"}
  p=${p//\\/\\\\}; p=${p//\"/\\\"}
  printf 'user = "%s:%s"\n' "$u" "$p"
}

api() {
  local method="$1"
  local path="$2"
  local user="$3"
  local password="$4"
  local content_type="${5:-}"
  local body_file="${6:-}"
  local args=(
    --silent
    --show-error
    --output /tmp/kiln-nexus-body
    --write-out '%{http_code}'
    -X "$method"
    "${API_BASE}${path}"
  )
  if [[ -n "$content_type" ]]; then
    args+=(-H "Content-Type: ${content_type}")
  fi
  if [[ -n "$body_file" ]]; then
    args+=(--data-binary "@${body_file}")
  fi
  local auth_config
  auth_config="$(curl_user_config "$user" "$password")" || exit 1
  printf '%s\n' "$auth_config" | curl -K - "${args[@]}"
}

expect() {
  local method="$1"
  local path="$2"
  local user="$3"
  local password="$4"
  local content_type="$5"
  local body_file="$6"
  shift 6
  local got code
  got="$(api "$method" "$path" "$user" "$password" "$content_type" "$body_file")"
  for code in "$@"; do
    if [[ "$got" == "$code" ]]; then
      return 0
    fi
  done
  echo "Nexus ${method} failed HTTP ${got}" >&2
  redact "$(cat /tmp/kiln-nexus-body 2>/dev/null || true)" >&2
  exit 1
}

accept_eula() {
  local code
  code="$(api GET "/service/rest/v1/system/eula" "$ADMIN_USER" "$ADMIN_PASSWORD")"
  if [[ "$code" != "200" ]]; then
    echo "Nexus EULA status failed HTTP ${code}" >&2
    redact "$(cat /tmp/kiln-nexus-body 2>/dev/null || true)" >&2
    exit 1
  fi
  python3 - <<'PY'
import json
import sys

with open("/tmp/kiln-nexus-body", encoding="utf-8") as handle:
    body = json.load(handle)
if body.get("accepted") is True:
    sys.exit(0)
disclaimer = body.get("disclaimer")
if not isinstance(disclaimer, str) or not disclaimer:
    sys.exit("Nexus EULA response has no disclaimer")
with open("/tmp/kiln-nexus-eula.json", "w", encoding="utf-8") as handle:
    json.dump({"accepted": True, "disclaimer": disclaimer}, handle)
PY
  if [[ ! -s /tmp/kiln-nexus-eula.json ]]; then
    echo "Nexus Community Edition EULA was already accepted."
    return 0
  fi
  expect POST "/service/rest/v1/system/eula" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-eula.json 200 204
  rm -f /tmp/kiln-nexus-eula.json
  echo "Accepted the Nexus Community Edition EULA."
}

ensure_jenkins_curl() {
  if ! docker inspect "$JENKINS_NAME" >/dev/null 2>&1; then
    echo "Jenkins container ${JENKINS_NAME} is not running; start scripts/ci-external-services.sh first" >&2
    exit 1
  fi
  if docker exec "$JENKINS_NAME" command -v curl >/dev/null 2>&1; then
    return 0
  fi
  docker exec -u root "$JENKINS_NAME" apk add --no-cache curl
}

start_proxy() {
  assert_isolated
  # Scratch files go away on every exit, including a failed start.
  # The env file stays so the next step can read it; stop removes that file.
  trap cleanup_proxy_temps EXIT
  stop_proxy
  ensure_jenkins_curl

  if ! docker network inspect "$CI_NETWORK" >/dev/null 2>&1; then
    docker network create "$CI_NETWORK" >/dev/null
  fi

  ADMIN_PASSWORD="$(openssl rand -hex 24)Aa1_"
  READER_PASSWORD="$(openssl rand -hex 24)Aa1_"
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::add-mask::${ADMIN_PASSWORD}"
    echo "::add-mask::${READER_PASSWORD}"
  fi

  docker run -d \
    --name "$NEXUS_NAME" \
    --network "$CI_NETWORK" \
    --memory 2g \
    -p "127.0.0.1:${API_PORT}:${CONTAINER_PORT}" \
    -e "INSTALL4J_ADD_VM_PARAMS=-Xms512m -Xmx1g -XX:MaxDirectMemorySize=1g" \
    -v "${NEXUS_VOLUME}:/nexus-data" \
    "$NEXUS_IMAGE"

  wait_for_ready
  OLD_ADMIN_PASSWORD="$(docker exec "$NEXUS_NAME" cat /nexus-data/admin.password | tr -d '\r\n')"
  if [[ -z "$OLD_ADMIN_PASSWORD" ]]; then
    echo "Nexus did not write an initial admin password" >&2
    exit 1
  fi
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::add-mask::${OLD_ADMIN_PASSWORD}"
  fi

  umask 077
  printf '%s' "$ADMIN_PASSWORD" >/tmp/kiln-nexus-pass
  expect PUT "/service/rest/v1/security/users/${ADMIN_USER}/change-password" \
    "$ADMIN_USER" "$OLD_ADMIN_PASSWORD" "text/plain" /tmp/kiln-nexus-pass 204
  rm -f /tmp/kiln-nexus-pass
  OLD_ADMIN_PASSWORD=""

  cat >/tmp/kiln-nexus-body.json <<'EOF'
{"enabled":false,"userId":"anonymous","realmName":"NexusAuthorizingRealm"}
EOF
  expect PUT "/service/rest/v1/security/anonymous" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-body.json 200 204

  accept_eula

  cat >/tmp/kiln-nexus-body.json <<EOF
{
  "name": "${HOSTED_NAME}",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": false,
    "writePolicy": "allow"
  },
  "component": {"proprietaryComponents": false},
  "raw": {"contentDisposition": "ATTACHMENT"}
}
EOF
  expect POST "/service/rest/v1/repositories/raw/hosted" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-body.json 201 200

  printf 'kiln-depproxy-fixture\n' >/tmp/kiln-nexus-fixture
  local auth_config
  auth_config="$(curl_user_config "$ADMIN_USER" "$ADMIN_PASSWORD")" || exit 1
  upload_code="$(
    printf '%s\n' "$auth_config" |
      curl -K - --silent --output /tmp/kiln-nexus-body --write-out '%{http_code}' \
      -F "raw.directory=ci" \
      -F "raw.asset1=@/tmp/kiln-nexus-fixture;filename=fixture.bin" \
      -F "raw.asset1.filename=fixture.bin" \
      "${API_BASE}/service/rest/v1/components?repository=${HOSTED_NAME}"
  )"
  if [[ "$upload_code" != "204" && "$upload_code" != "201" ]]; then
    echo "Nexus fixture upload failed HTTP ${upload_code}" >&2
    redact "$(cat /tmp/kiln-nexus-body 2>/dev/null || true)" >&2
    exit 1
  fi

  cat >/tmp/kiln-nexus-body.json <<EOF
{
  "name": "${GROUP_NAME}",
  "online": true,
  "storage": {
    "blobStoreName": "default",
    "strictContentTypeValidation": false
  },
  "group": {"memberNames": ["${HOSTED_NAME}"]},
  "raw": {"contentDisposition": "ATTACHMENT"}
}
EOF
  expect POST "/service/rest/v1/repositories/raw/group" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-body.json 201 200

  cat >/tmp/kiln-nexus-body.json <<EOF
{
  "id": "${ROLE_NAME}",
  "name": "${ROLE_NAME}",
  "description": "read the CI raw group",
  "privileges": [
    "nx-repository-view-raw-${GROUP_NAME}-browse",
    "nx-repository-view-raw-${GROUP_NAME}-read"
  ],
  "roles": []
}
EOF
  expect POST "/service/rest/v1/security/roles" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-body.json 200 201

  cat >/tmp/kiln-nexus-user.json <<EOF
{
  "userId": "${READER_USER}",
  "firstName": "Kiln",
  "lastName": "Reader",
  "emailAddress": "kiln-ci-reader@localhost",
  "password": "${READER_PASSWORD}",
  "status": "active",
  "roles": ["${ROLE_NAME}"]
}
EOF
  expect POST "/service/rest/v1/security/users" \
    "$ADMIN_USER" "$ADMIN_PASSWORD" "application/json" /tmp/kiln-nexus-user.json 200 201
  rm -f /tmp/kiln-nexus-user.json /tmp/kiln-nexus-body.json

  auth_config="$(curl_user_config "$READER_USER" "$READER_PASSWORD")" || exit 1
  reader_code="$(
    printf '%s\n' "$auth_config" |
      curl -K - --silent --output /tmp/kiln-nexus-fixture-got --write-out '%{http_code}' \
      "${API_BASE}/repository/${GROUP_NAME}/${FIXTURE_PATH}"
  )"
  if [[ "$reader_code" != "200" ]]; then
    echo "read-only user could not download the fixture HTTP ${reader_code}" >&2
    exit 1
  fi
  if ! cmp -s /tmp/kiln-nexus-fixture /tmp/kiln-nexus-fixture-got; then
    echo "fixture bytes downloaded through the group did not match" >&2
    exit 1
  fi

  anon_code="$(
    curl --silent --output /dev/null --write-out '%{http_code}' \
      "${API_BASE}/repository/${GROUP_NAME}/${FIXTURE_PATH}" || true
  )"
  if [[ "$anon_code" != "401" && "$anon_code" != "403" ]]; then
    echo "anonymous read returned HTTP ${anon_code}" >&2
    exit 1
  fi

  auth_config="$(curl_user_config "$READER_USER" "$READER_PASSWORD")" || exit 1
  list_code="$(
    printf '%s\n' "$auth_config" |
      curl -K - --silent --output /tmp/kiln-nexus-repos.json --write-out '%{http_code}' \
      "${API_BASE}/service/rest/v1/repositories" || true
  )"
  auth_config="$(curl_user_config "$READER_USER" "$READER_PASSWORD")" || exit 1
  root_code="$(
    printf '%s\n' "$auth_config" |
      curl -K - --silent --output /dev/null --write-out '%{http_code}' \
      "${API_BASE}/repository/${GROUP_NAME}/" || true
  )"
  KILN_NEXUS_LIST_CODE="$list_code" \
    KILN_NEXUS_GROUP="$GROUP_NAME" \
    python3 - <<'PY'
import json
import os

code = os.environ["KILN_NEXUS_LIST_CODE"]
name = os.environ["KILN_NEXUS_GROUP"]
listed = "no"
try:
    data = json.load(open("/tmp/kiln-nexus-repos.json", encoding="utf-8"))
except (OSError, ValueError):
    data = None
if isinstance(data, list) and any(
    isinstance(item, dict) and item.get("name") == name for item in data
):
    listed = "yes"
print(f"reader repository list HTTP {code}; group listed: {listed}")
PY
  echo "reader repository root HTTP ${root_code}"

  local env_file="${KILN_CI_NEXUS_ENV_FILE:-/tmp/kiln-ci-nexus.env}"
  umask 077
  cat >"$env_file" <<EOF
KILN_TEST_NEXUS_URL=${API_BASE}
KILN_TEST_NEXUS_BUILD_URL=${BUILD_BASE}
KILN_TEST_NEXUS_REPOSITORY=${GROUP_NAME}
KILN_TEST_NEXUS_USERNAME=${READER_USER}
KILN_TEST_NEXUS_PASSWORD=${READER_PASSWORD}
KILN_TEST_NEXUS_ADMIN_USER=${ADMIN_USER}
KILN_TEST_NEXUS_ADMIN_PASSWORD=${ADMIN_PASSWORD}
KILN_TEST_NEXUS_HOSTED_REPOSITORY=${HOSTED_NAME}
KILN_TEST_NEXUS_FIXTURE_PATH=${FIXTURE_PATH}
KILN_REQUIRE_NEXUS_TESTS=1
EOF
  chmod 600 "$env_file"
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    cat "$env_file" >>"$GITHUB_ENV"
  fi
  echo "Throwaway dependency proxy is on ${api_host}:${API_PORT} (${NEXUS_NAME})."
  echo "Jenkins reaches the group ${GROUP_NAME} on ${BUILD_BASE}."
  echo "Credentials were written to ${env_file} and were not printed."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  if [[ -z "${GITHUB_ACTIONS:-}" && -z "${CI:-}" ]]; then
    echo "只在 CI 中运行" >&2
    exit 1
  fi

  case "${1:-}" in
    start) start_proxy ;;
    stop) stop_proxy ;;
    *)
      echo "usage: $0 start|stop" >&2
      exit 2
      ;;
  esac
fi
