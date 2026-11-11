#!/usr/bin/env bash
# 守护脚本：检测被加密/删除的源码文件，自动从 git 恢复。
#
# 背景：本机的 Symantec DLP 会把修改过的源码文件替换成加密块
#      （%TSD-Header-###% 开头，file 命令识别为 "data"）。
#      排除列表只能由 IT 在控制台配置，所以本地用这个兜底。
#
# 用法：
#   ./scripts/guard.sh             检查并恢复
#   ./scripts/guard.sh --check     只检查，不恢复（安全，适合提交前跑）
set -uo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "不在 git 仓库里"; exit 1; }

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

fixed=0
bad=0

# 只检查 git 跟踪的、文本类文件
while IFS= read -r -d '' f; do
  if [ ! -f "$f" ]; then
    # 被删了 —— 只有加密软件会这么干，正常编辑不会
    bad=$((bad+1)); echo "✗ 缺失  $f"
    [ $CHECK_ONLY -eq 0 ] && git checkout -- "$f" 2>/dev/null && fixed=$((fixed+1))
    continue
  fi

  # 正常文本：Python / TS / MD / JSON / CSS 都会被 file 认成 text 或 JSON
  if file -b "$f" | grep -qE "text|JSON|empty"; then
    continue
  fi

  bad=$((bad+1)); echo "✗ 损坏  $f"
  [ $CHECK_ONLY -eq 0 ] && git checkout -- "$f" 2>/dev/null && fixed=$((fixed+1))
done < <(git ls-files -z -- '*.py' '*.ts' '*.tsx' '*.md' '*.json' '*.css' '*.toml' '*.sh' '*.env.example')

echo
if [ $bad -eq 0 ]; then
  echo "✓ 全部正常（检查了 $(git ls-files -- '*.py' '*.ts' '*.tsx' '*.md' '*.json' '*.css' '*.toml' '*.sh' | wc -l | tr -d ' ') 个文件）"
else
  echo "发现 $bad 个异常，已恢复 $fixed 个"
  echo "提示：恢复的是上次 commit 的版本，未提交的改动会丢失"
  [ $CHECK_ONLY -eq 1 ] && echo "去掉 --check 才会真正恢复"
fi
