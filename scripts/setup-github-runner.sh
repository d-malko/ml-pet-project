#!/usr/bin/env bash
# scripts/setup-github-runner.sh
#
# Устанавливает GitHub Actions self-hosted runner на локальную машину.
#
# Использование:
#   export GITHUB_REPO="owner/repo"        # например: thund3r/ml-pet-project
#   export GITHUB_TOKEN="ghp_xxxxxxxxxxxx" # нужны права: repo (или workflow)
#   bash scripts/setup-github-runner.sh

set -euo pipefail

GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date +%T)] ✅ $*${NC}"; }
info() { echo -e "${BLUE}[$(date +%T)] ℹ️  $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date +%T)] ⚠️  $*${NC}"; }
err()  { echo -e "${RED}[$(date +%T)] ❌ $*${NC}"; exit 1; }

# ── Проверка переменных ───────────────────────────────────────────
: "${GITHUB_REPO:?Укажи GITHUB_REPO=owner/repo}"
: "${GITHUB_TOKEN:?Укажи GITHUB_TOKEN=ghp_xxx}"

RUNNER_VERSION="2.323.0"
RUNNER_DIR="${HOME}/actions-runner"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-minikube}"

# ── Проверка зависимостей ─────────────────────────────────────────
info "Checking dependencies..."
for cmd in kubectl minikube kubectl-argo-rollouts python3 docker; do
    if command -v ${cmd} &>/dev/null; then
        log "${cmd} found"
    else
        warn "${cmd} not found — установи перед запуском jobs"
    fi
done

# ── Валидация токена и репозитория ────────────────────────────────
info "Validating GITHUB_REPO=${GITHUB_REPO} and token..."

API_URL="https://api.github.com/repos/${GITHUB_REPO}"
HTTP_CODE=$(curl -so /tmp/gh_repo_check.json -w "%{http_code}" \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "${API_URL}")

if [[ "${HTTP_CODE}" == "404" ]]; then
    echo ""
    err "Репозиторий '${GITHUB_REPO}' не найден (404).
    Проверь:
      1. GITHUB_REPO='owner/repo' — правильное написание (регистр важен)
      2. Токен имеет доступ к репозиторию (scope: repo)
      3. Для private репо токен должен иметь scope: repo
         Для public репо достаточно scope: public_repo
    Создать токен: https://github.com/settings/tokens/new"
elif [[ "${HTTP_CODE}" == "401" ]]; then
    err "Неверный токен (401). Создай новый: https://github.com/settings/tokens/new
    Нужные scopes: repo (или workflow для Actions)"
elif [[ "${HTTP_CODE}" != "200" ]]; then
    cat /tmp/gh_repo_check.json
    err "GitHub API вернул HTTP ${HTTP_CODE}"
fi

REPO_FULL=$(python3 -c "import json; d=json.load(open('/tmp/gh_repo_check.json')); print(d['full_name'])")
log "Repository found: ${REPO_FULL}"

# ── Скачиваем runner (если ещё не скачан) ────────────────────────
if [[ -f "${RUNNER_DIR}/run.sh" ]]; then
    warn "Runner already exists at ${RUNNER_DIR}, skipping download"
else
    info "Downloading GitHub Actions runner v${RUNNER_VERSION}..."
    mkdir -p "${RUNNER_DIR}"
    cd "${RUNNER_DIR}"

    curl -fsSL \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
        -o runner.tar.gz
    tar xzf runner.tar.gz
    rm runner.tar.gz
    log "Runner downloaded to ${RUNNER_DIR}"
fi

cd "${RUNNER_DIR}"

# ── Получаем registration token ───────────────────────────────────
info "Getting registration token..."

TOKEN_RESPONSE=$(curl -fsX POST \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/${GITHUB_REPO}/actions/runners/registration-token")

REG_TOKEN=$(echo "${TOKEN_RESPONSE}" | python3 -c \
    "import sys, json; print(json.load(sys.stdin)['token'])" 2>/dev/null || true)

if [[ -z "${REG_TOKEN}" ]]; then
    echo "API response: ${TOKEN_RESPONSE}"
    err "Не удалось получить registration token.
    Проверь что токен имеет scope: repo (не только public_repo)"
fi
log "Registration token received"

# ── Регистрируем runner ───────────────────────────────────────────
info "Registering runner as '${RUNNER_NAME}'..."

./config.sh \
    --url "https://github.com/${GITHUB_REPO}" \
    --token "${REG_TOKEN}" \
    --name "${RUNNER_NAME}" \
    --labels "self-hosted,minikube,linux" \
    --work "_work" \
    --unattended \
    --replace

log "Runner registered: ${RUNNER_NAME}"

# ── Устанавливаем как systemd service ────────────────────────────
info "Installing systemd service..."
sudo ./svc.sh install
sudo ./svc.sh start
log "Service installed and started"

# ── Финальный статус ──────────────────────────────────────────────
echo ""
sudo ./svc.sh status

echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🎉 Self-hosted runner ready!${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo "  Runner:  ${RUNNER_NAME}"
echo "  Labels:  self-hosted, minikube, linux"
echo "  Dir:     ${RUNNER_DIR}"
echo ""
echo "  Проверь в браузере:"
echo "  https://github.com/${GITHUB_REPO}/settings/actions/runners"
echo "  Runner должен быть со статусом Idle ✅"
echo ""
echo "  Управление:"
echo "    sudo ${RUNNER_DIR}/svc.sh status|stop|start"
echo "    journalctl -u actions.runner.*.service -f"
echo ""
echo "  Удалить runner:"
echo "    cd ${RUNNER_DIR} && ./config.sh remove --token <new_token>"
