#!/bin/bash
# MACD 背离监控 - 部署到 GitHub Pages
# 用法: bash deploy.sh
# 说明: 首次运行时从 /tmp/ghtoken 读取 token 配置 remote（写入 .git/config，持久化）；
#       之后的 git push 走 .git/config 里的 insteadOf，无需再次提供 token。
set -e
cd /Users/beanpaper/WorkBuddy/2026-07-27-23-08-55

# 代理环境下 git over HTTP/2 的 CONNECT 会被拦截，强制 HTTP/1.1
git config http.version HTTP/1.1

if [ ! -d .git ]; then
  git init -q
  git branch -M main
fi

TOKEN=$(cat /tmp/ghtoken 2>/dev/null)
REMOTE="https://github.com/autumn-go/macd-divergence-monitor.git"

if [ -n "$TOKEN" ]; then
  # 把 token 嵌进 .git/config 的 insteadOf，remote URL 保持干净
  git config url."https://x-access-token:${TOKEN}@github.com/".insteadOf "https://github.com/"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "$REMOTE"
fi

git config user.email "autumn-go@users.noreply.github.com" 2>/dev/null || true
git config user.name "autumn-go" 2>/dev/null || true

# 站点真正需要的文件 + 可复现的脚本
git add index.html assets/echarts.min.js data/macd_data.js macd_monitor.py refill.py deploy.sh feishu_push.py README.md .gitignore 2>/dev/null

if git diff --cached --quiet; then
  echo "无变更，跳过提交"
else
  git commit -q -m "deploy: $(date +%F_%T)"
  echo "已提交"
fi

git push -u origin main
echo "DEPLOY DONE -> https://autumn-go.github.io/macd-divergence-monitor/"
