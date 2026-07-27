#!/usr/bin/env bash
# Created by model-proxy on 2026/05/21
# Copyright © 2026
#
# Pre-commit hook: 扫描暂存区文件是否包含疑似 API Key / Secret
# 安装: cp scripts/pre-commit-secrets-scan.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
# 或:   ln -sf ../../scripts/pre-commit-secrets-scan.sh .git/hooks/pre-commit

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PATTERNS=(
    'AIzaSy[a-zA-Z0-9_\-]{30,}'
    'gsk_[a-zA-Z0-9]{20,}'
    'github_pat_[a-zA-Z0-9_]{20,}'
    'ghp_[a-zA-Z0-9]{30,}'
    'sk-[a-zA-Z0-9]{20,}'
    'hf_[a-zA-Z0-9]{20,}'
    'glpat-[a-zA-Z0-9\-]{20,}'
    'xoxb-[a-zA-Z0-9\-]{20,}'
    'AKIA[0-9A-Z]{16}'
)

ALLOWLIST_FILES=(
    'src/api/log_buffer.py'
    'src/scheduler/memory.py'
    'tests/'
    'scripts/pre-commit-secrets-scan.sh'
)

found_secrets=0

staged_files=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || true)
if [ -z "$staged_files" ]; then
    exit 0
fi

for file in $staged_files; do
    skip=false
    for allow in "${ALLOWLIST_FILES[@]}"; do
        if [[ "$file" == *"$allow"* ]]; then
            skip=true
            break
        fi
    done
    if [ "$skip" = true ]; then
        continue
    fi

    content=$(git show ":$file" 2>/dev/null || true)
    if [ -z "$content" ]; then
        continue
    fi

    for pattern in "${PATTERNS[@]}"; do
        matches=$(echo "$content" | grep -nEo "$pattern" 2>/dev/null || true)
        if [ -n "$matches" ]; then
            echo -e "${RED}[BLOCKED]${NC} 疑似 API Key 在 ${YELLOW}$file${NC}:"
            echo "$matches" | head -3
            found_secrets=1
        fi
    done
done

if [ $found_secrets -ne 0 ]; then
    echo ""
    echo -e "${RED}提交被阻止：检测到疑似敏感凭据！${NC}"
    echo "如确认为误报，可使用: git commit --no-verify"
    echo "请确保敏感数据仅存放在 .gitignore 排除的文件中"
    exit 1
fi

exit 0
