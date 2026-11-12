#!/usr/bin/env bash
# Start or stop throwaway Jenkins and Gitea for CI.
# Container names, volume names, and host ports are distinct from the shared lab
# (kiln-postgres / kiln-gitea / kiln-jenkins and 5432 / 3000 / 2222 / 8080 / 50000)
# and from the repo compose lab profile (18080 / 13000).
# Passwords are generated here and are not written into the repository.

set -euo pipefail

JENKINS_NAME="kiln-ci-jenkins"
GITEA_NAME="kiln-ci-gitea"
JENKINS_VOLUME="kiln-ci-jenkins-data"
GITEA_VOLUME="kiln-ci-gitea-data"
JENKINS_PORT="28080"
GITEA_PORT="23000"
GITEA_SSH_PORT="2322"
JENKINS_USER="kiln-ci"
GITEA_USER="kiln-ci"
JENKINS_IMAGE="jenkins/jenkins:2.580.2-lts-alpine"
GITEA_IMAGE="gitea/gitea:1.24.7"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

assert_isolated() {
  local name volume port
  for name in "$JENKINS_NAME" "$GITEA_NAME"; do
    case "$name" in
      kiln-postgres | kiln-gitea | kiln-jenkins | kiln-api | kiln-console)
        echo "refusing to touch lab container ${name}" >&2
        exit 1
        ;;
    esac
  done
  for volume in "$JENKINS_VOLUME" "$GITEA_VOLUME"; do
    case "$volume" in
      kiln-postgres-data | kiln-gitea-data | kiln-jenkins-data | kiln-jenkins-home)
        echo "refusing to touch lab volume ${volume}" >&2
        exit 1
        ;;
    esac
  done
  for port in "$JENKINS_PORT" "$GITEA_PORT" "$GITEA_SSH_PORT"; do
    case "$port" in
      5432 | 3000 | 2222 | 8080 | 50000 | 8000 | 18080 | 13000)
        echo "refusing to bind lab or repo-compose port ${port}" >&2
        exit 1
        ;;
    esac
  done
}

redact() {
  local text="$1"
  if [[ -n "${JENKINS_ADMIN_PASSWORD:-}" ]]; then
    text="${text//${JENKINS_ADMIN_PASSWORD}/***}"
  fi
  if [[ -n "${GITEA_ADMIN_PASSWORD:-}" ]]; then
    text="${text//${GITEA_ADMIN_PASSWORD}/***}"
  fi
  if [[ -n "${GITEA_TOKEN:-}" ]]; then
    text="${text//${GITEA_TOKEN}/***}"
  fi
  printf '%s\n' "$text"
}

stop_services() {
  assert_isolated
  docker rm -f "$JENKINS_NAME" "$GITEA_NAME" >/dev/null 2>&1 || true
  docker volume rm "$JENKINS_VOLUME" "$GITEA_VOLUME" >/dev/null 2>&1 || true
}

wait_for_url() {
  local url="$1"
  local attempts="$2"
  local i
  for i in $(seq 1 "$attempts"); do
    if curl --silent --fail --output /dev/null "$url"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

start_services() {
  assert_isolated
  stop_services

  JENKINS_ADMIN_PASSWORD="$(openssl rand -hex 24)"
  GITEA_ADMIN_PASSWORD="$(openssl rand -hex 24)"
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::add-mask::${JENKINS_ADMIN_PASSWORD}"
    echo "::add-mask::${GITEA_ADMIN_PASSWORD}"
  fi

  docker run -d \
    --name "$JENKINS_NAME" \
    -p "127.0.0.1:${JENKINS_PORT}:8080" \
    -e "JENKINS_ADMIN_ID=${JENKINS_USER}" \
    -e "JENKINS_ADMIN_PASSWORD=${JENKINS_ADMIN_PASSWORD}" \
    -e "JAVA_OPTS=-Xmx1g -Djenkins.install.runSetupWizard=false -Dhudson.security.csrf.DefaultCrumbIssuer.EXCLUDE_SESSION_ID=true" \
    -v "${JENKINS_VOLUME}:/var/jenkins_home" \
    -v "${ROOT}/scripts/jenkins/init.groovy.d:/usr/share/jenkins/ref/init.groovy.d:ro" \
    "$JENKINS_IMAGE"

  docker run -d \
    --name "$GITEA_NAME" \
    -p "127.0.0.1:${GITEA_PORT}:3000" \
    -e GITEA__database__DB_TYPE=sqlite3 \
    -e GITEA__database__PATH=/data/gitea/gitea.db \
    -e GITEA__server__DOMAIN=127.0.0.1 \
    -e "GITEA__server__ROOT_URL=http://127.0.0.1:${GITEA_PORT}/" \
    -e GITEA__server__HTTP_PORT=3000 \
    -e GITEA__server__DISABLE_SSH=true \
    -e GITEA__security__INSTALL_LOCK=true \
    -e GITEA__service__DISABLE_REGISTRATION=true \
    -e GITEA__repository__DEFAULT_BRANCH=main \
    -v "${GITEA_VOLUME}:/data" \
    "$GITEA_IMAGE"

  if ! wait_for_url "http://127.0.0.1:${GITEA_PORT}/api/healthz" 60; then
    echo "Gitea did not become healthy" >&2
    redact "$(docker logs "$GITEA_NAME" 2>&1 | tail -n 80)" >&2
    exit 1
  fi

  local attempt output
  for attempt in $(seq 1 30); do
    if output="$(
      docker exec -u git "$GITEA_NAME" \
        gitea admin user create \
        --config /data/gitea/conf/app.ini \
        --admin \
        --username "$GITEA_USER" \
        --password "$GITEA_ADMIN_PASSWORD" \
        --email kiln-ci@localhost \
        --must-change-password=false \
        2>&1
    )"; then
      break
    fi
    if [[ "$output" == *"already exists"* ]]; then
      break
    fi
    if [[ "$attempt" == "30" ]]; then
      echo "Gitea admin user was not created" >&2
      redact "$output" >&2
      exit 1
    fi
    sleep 2
  done

  output=""
  for attempt in $(seq 1 15); do
    if output="$(
      docker exec -u git "$GITEA_NAME" \
        gitea admin user generate-access-token \
        --config /data/gitea/conf/app.ini \
        --username "$GITEA_USER" \
        --token-name "kiln-ci-${attempt}" \
        --scopes all \
        --raw \
        2>&1
    )"; then
      break
    fi
    if [[ "$attempt" == "15" ]]; then
      echo "Gitea access token was not created" >&2
      redact "$output" >&2
      exit 1
    fi
    sleep 2
  done
  GITEA_TOKEN="$(printf '%s\n' "$output" | tr -d '\r' | awk 'NF { line=$0 } END { print line }')"
  GITEA_TOKEN="${GITEA_TOKEN##* }"
  if [[ ${#GITEA_TOKEN} -lt 20 || "$GITEA_TOKEN" == *" "* || "$GITEA_TOKEN" == *$'\n'* ]]; then
    echo "Gitea did not return a usable access token" >&2
    exit 1
  fi
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::add-mask::${GITEA_TOKEN}"
  fi

  if ! curl --silent --fail --output /dev/null \
    --header "Authorization: token ${GITEA_TOKEN}" \
    "http://127.0.0.1:${GITEA_PORT}/api/v1/user"; then
    echo "Gitea token could not read /api/v1/user" >&2
    exit 1
  fi

  local ready=0 anon_code auth_code
  for attempt in $(seq 1 120); do
    anon_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
      "http://127.0.0.1:${JENKINS_PORT}/api/json" || true)"
    auth_code="$(curl --silent --output /dev/null --write-out '%{http_code}' \
      --user "${JENKINS_USER}:${JENKINS_ADMIN_PASSWORD}" \
      "http://127.0.0.1:${JENKINS_PORT}/api/json" || true)"
    if [[ "$anon_code" =~ ^(401|403)$ && "$auth_code" == "200" ]]; then
      ready=1
      break
    fi
    sleep 2
  done
  if [[ "$ready" != "1" ]]; then
    echo "Jenkins did not accept the generated admin credentials" >&2
    redact "$(docker logs "$JENKINS_NAME" 2>&1 | tail -n 80)" >&2
    exit 1
  fi

  local env_file="${KILN_CI_ENV_FILE:-/tmp/kiln-ci-external.env}"
  umask 077
  cat >"$env_file" <<EOF
KILN_TEST_JENKINS_URL=http://127.0.0.1:${JENKINS_PORT}
KILN_TEST_JENKINS_USER=${JENKINS_USER}
KILN_TEST_JENKINS_API_TOKEN=${JENKINS_ADMIN_PASSWORD}
KILN_TEST_GIT_BASE_URL=http://127.0.0.1:${GITEA_PORT}
KILN_TEST_GIT_TOKEN=${GITEA_TOKEN}
KILN_TEST_GIT_PROVIDER=gitea
EOF
  chmod 600 "$env_file"
  if [[ -n "${GITHUB_ENV:-}" ]]; then
    cat "$env_file" >>"$GITHUB_ENV"
  fi
  echo "Throwaway Jenkins is on 127.0.0.1:${JENKINS_PORT} (${JENKINS_NAME})."
  echo "Throwaway Gitea is on 127.0.0.1:${GITEA_PORT} (${GITEA_NAME})."
  echo "Credentials were written to ${env_file} and were not printed."
}

case "${1:-}" in
  start) start_services ;;
  stop) stop_services ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
