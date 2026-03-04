#!/usr/bin/env bash
# scripts/setup-github-runner.sh
#
# Устанавливает GitHub Actions self-hosted runner на локальную машину.
# Runner получает лейбл "self-hosted,minikube,linux" и регистрируется
# как systemd сервис (автостарт при перезагрузке).
#
# Требования:
#   - Ubuntu 20.04+ / Debian
#   - minikube, kubectl, kubectl-argo-rollouts уже установлены
#   - Пользователь имеет права sudo
#
# Использование:
#   export GITHUB_REPO="owner/repo"        # например: thund3r/ml-pet-project
#   export GITHUB_TOKEN="ghp_xxxxxxxxxxxx" # GitHub → Settings → Actions → Runners → New runner
#   bash scripts/setup-github-runner.sh

set -euo pipefail

# ── Цвета для вывода ──────────────────────────────────────────────
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
    if ! command -v ${cmd} &>/dev/null; then
        warn "${cmd} not found — make sure it's installed before running jobs"
    else
        log "${cmd}: $(${cmd} version --client 2>/dev/null | head -1 || ${cmd} --version 2>&1 | head -1)"
    fi
done

# ── Скачиваем runner ──────────────────────────────────────────────
info "Downloading GitHub Actions runner v${RUNNER_VERSION}..."

mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"

curl -fsSL "${RUNNER_URL}" -o runner.tar.gz
tar xzf runner.tar.gz
rm runner.tar.gz
log "Runner downloaded to ${RUNNER_DIR}"

# ── Получаем registration token ───────────────────────────────────
info "Getting registration token from GitHub API..."
REG_TOKEN=$(curl -fsSX POST \
    -H "Authorization: token ${GITHUB_TOKEN}" \
    -H "Accept: application/vnd.github.v3+json" \
    "https://api.github.com/repos/${GITHUB_REPO}/actions/runners/registration-token" \
    | python3 -c "import sys, json; print(json.load(sys.stdin)['token'])")

if [[ -z "${REG_TOKEN}" ]]; then
    err "Failed to get registration token. Check GITHUB_TOKEN and GITHUB_REPO."
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
    --replace    # заменяет runner с тем же именем если уже существует

log "Runner registered: ${RUNNER_NAME}"

# ── Устанавливаем как systemd service ────────────────────────────
info "Installing systemd service..."
sudo ./svc.sh install
sudo ./svc.sh start
log "Service installed and started"

# ── Проверка ──────────────────────────────────────────────────────
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
echo "  GitHub → Settings → Actions → Runners"
echo "  Там должен появиться runner со статусом Idle ✅"
echo ""
echo "  Управление сервисом:"
echo "    sudo ${RUNNER_DIR}/svc.sh status"
echo "    sudo ${RUNNER_DIR}/svc.sh stop"
echo "    sudo ${RUNNER_DIR}/svc.sh start"
echo ""
echo "  Логи:"
echo "    journalctl -u actions.runner.*.service -f"
echo ""
echo "  Удалить runner:"
echo "    cd ${RUNNER_DIR} && ./config.sh remove --token <token>"
