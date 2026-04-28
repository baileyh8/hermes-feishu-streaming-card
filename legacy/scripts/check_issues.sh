#!/bin/bash
# 检查 hermes-feishu-streaming-card 项目的新 issues
# 只监控 v2.2 (2026-04-19) 之后的反馈

REPO="baileyh8/hermes-feishu-streaming-card"
SINCE="2026-04-19"
LOG="/tmp/hermes-fsc-issue-check.log"

# 获取 v2.2+ 的 open issues
issues=$(curl -s "https://api.github.com/repos/$REPO/issues?state=open&since=$SINCE" 2>/dev/null)

count=$(echo "$issues" | python3 -c "
import json, sys
data = json.load(sys.stdin)
# 过滤掉 pull requests
issues = [i for i in data if not i.get('pull_request')]
print(len(issues))
")

if [ "$count" -gt 0 ]; then
    echo "发现 $count 个 v2.2+ 新 issue" >> "$LOG"
    echo "$issues" | python3 -c "
import json, sys
issues = json.load(sys.stdin)
issues = [i for i in issues if not i.get('pull_request')]
for issue in issues[:5]:
    print(f\"#{issue['number']}: {issue['title']}\")
    print(f\"   标签: {', '.join([l['name'] for l in issue.get('labels', [])]) or '无'}\")
    print()
" >> "$LOG"
    
    # 发送通知到飞书（如果有配置）
    if [ -n "\$FEISHU_WEBHOOK" ]; then
        curl -s -X POST "$FEISHU_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"🦐 Feishu Streaming Card 项目有 $count 个新 issue\\nhttps://github.com/$REPO/issues\"}}"
    fi
else
    echo "$(date): 暂无新 issue" >> "$LOG"
fi
