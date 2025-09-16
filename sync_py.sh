#!/usr/bin/env bash

set -euo pipefail

# Sync only Python (.py) files to a remote server using rsync over SSH.
# Reads connection info from .env at repository root.
# Required vars in .env: REMOTE_HOST, REMOTE_USER, REMOTE_PASSWORD, REMOTE_PATH
# Optional vars: REMOTE_PORT (default 22), SOURCE_DIR (default repo root)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERROR] $ENV_FILE 不存在，请先根据 .env.example 创建并填写。"
  exit 1
fi

# Load .env safely (supports quoted values)
set -a
source "$ENV_FILE"
set +a

: "${REMOTE_HOST:?请在 .env 中设置 REMOTE_HOST}"
: "${REMOTE_USER:?请在 .env 中设置 REMOTE_USER}"
: "${REMOTE_PASSWORD:?请在 .env 中设置 REMOTE_PASSWORD}"
: "${REMOTE_PATH:?请在 .env 中设置 REMOTE_PATH}"
REMOTE_PORT=${REMOTE_PORT:-22}
SOURCE_DIR=${SOURCE_DIR:-"$SCRIPT_DIR"}

if ! command -v rsync >/dev/null 2>&1; then
  echo "[ERROR] 未找到 rsync，请先安装。macOS 可用: brew install rsync"
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "[ERROR] 未找到 sshpass（用于提供 SSH 密码）。"
  echo "macOS 可尝试安装："
  echo "  brew install hudochenkov/sshpass/sshpass    # 常用 tap"
  echo "或（若可用）"
  echo "  brew install sshpass"
  exit 1
fi

# Prepare a temporary password file for sshpass to avoid shell escaping issues
PASS_FILE=$(mktemp)
trap 'rm -f "$PASS_FILE"' EXIT
printf "%s" "$REMOTE_PASSWORD" > "$PASS_FILE"

# rsync over SSH settings
RSYNC_SSH=("ssh" "-o" "StrictHostKeyChecking=no" "-p" "$REMOTE_PORT")

# Ensure remote dir exists then run rsync
SSH_DEST="$REMOTE_USER@$REMOTE_HOST:$REMOTE_PATH"

# Build rsync args
RSYNC_ARGS=(
  -avz
  --include '*/'
  --include '*.py'
  --include 'requirements.txt'
  --exclude '*.pyc'
  --exclude '*__pycache__/*'
  --exclude '.git/*'
  --exclude '.env'
  --exclude '.DS_Store'
  --exclude '*'
  -e "${RSYNC_SSH[*]}"
  --rsync-path="mkdir -p '$REMOTE_PATH' && rsync"
)

# Optional: enable delete on remote if RSYNC_DELETE=1
if [[ "${RSYNC_DELETE:-0}" == "1" ]]; then
  RSYNC_ARGS+=("--delete")
fi

# Support DRY_RUN=1 to preview changes
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  RSYNC_ARGS+=("--dry-run")
  echo "[INFO] DRY_RUN=1 仅预览变更，不会真正上传。"
fi

echo "[INFO] 同步源目录: $SOURCE_DIR -> 远端: $SSH_DEST (仅 *.py 与 requirements.txt)"

sshpass -f "$PASS_FILE" rsync "${RSYNC_ARGS[@]}" \
  "$SOURCE_DIR"/ \
  "$SSH_DEST"

echo "[DONE] 同步完成。"
