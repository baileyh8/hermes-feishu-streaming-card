#!/bin/bash
#
# feishu-setup.sh — 飞书流式卡片环境配置助手
# 用途：交互式获取飞书应用凭证，生成配置文件
# 用法：bash feishu-setup.sh
#

set -e

HERMES_DIR="$HOME/.hermes"
CONFIG_SRC="$(cd "$(dirname "$0")" && pwd)/config.yaml.example"
CONFIG_DEST="$HERMES_DIR/feishu-sidecar.yaml"
ENV_FILE="$HERMES_DIR/.env"

echo "═══════════════════════════════════════════════════════════════"
echo "  Feishu Streaming Card — Environment Setup"
echo "═══════════════════════════════════════════════════════════════"
echo ""

# 检查依赖
if ! command -v curl &>/dev/null; then
    echo "❌ curl 未安装，请先安装 curl"
    exit 1
fi

# 步骤 1：指导用户创建飞书应用
cat <<'EOF'
📋 步骤 1/3：创建飞书应用

1. 打开飞书开放平台：https://open.feishu.cn/
2. 进入「我的应用」→「创建应用」
3. 应用类型：选择「自定义机器人」或「应用」
4. 填写应用名称、描述
5. 在「权限管理」中添加以下权限：
   - im:chatinfo (读取群聊信息)
   - im:message (发送/接收消息)
   - im:interactive (发送卡片消息)
   - contact:user (读取用户信息，可选)
6. 在「应用主页」复制：
   - App ID (来自「凭证与基础信息」)
   - App Secret (点击「显示」)
7. 启用「卡片消息」功能（在「功能」页面）

EOF

read -p "是否已完成飞书应用创建？(y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "请先完成飞书应用创建，然后重新运行此脚本。"
    exit 1
fi

# 步骤 2：输入 App ID 和 Secret
echo ""
echo "📝 步骤 2/3：输入应用凭证"
read -p "App ID: " APP_ID
read -sp "App Secret: " APP_SECRET
echo ""

# 验证输入
if [[ -z "$APP_ID" || -z "$APP_SECRET" ]]; then
    echo "❌ App ID 和 Secret 不能为空"
    exit 1
fi

# 步骤 3：生成配置文件
echo ""
echo "📦 步骤 3/3：生成配置文件"
cp "$CONFIG_SRC" "$CONFIG_DEST"
echo "✓ 已复制配置模板到 $CONFIG_DEST"

# 替换占位符
sed -i '' "s/app_id: \"\"/app_id: \"$APP_ID\"/" "$CONFIG_DEST"
sed -i '' "s/app_secret: \"\"/app_secret: \"$APP_SECRET\"/" "$CONFIG_DEST"
echo "✓ 已写入 App ID 和 Secret"

# 可选：更新 .env 文件
if [[ -f "$ENV_FILE" ]]; then
    echo ""
    read -p "是否将凭证写入 $ENV_FILE 以便其他工具使用？(y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if grep -q "FEISHU_APP_ID" "$ENV_FILE"; then
            sed -i '' "s/^FEISHU_APP_ID=.*/FEISHU_APP_ID=$APP_ID/" "$ENV_FILE"
            sed -i '' "s/^FEISHU_APP_SECRET=.*/FEISHU_APP_SECRET=$APP_SECRET/" "$ENV_FILE"
        else
            echo "FEISHU_APP_ID=$APP_ID" >> "$ENV_FILE"
            echo "FEISHU_APP_SECRET=$APP_SECRET" >> "$ENV_FILE"
        fi
        echo "✓ 已更新 $ENV_FILE"
    fi
fi

# 步骤 4：验证配置
echo ""
echo "🔍 验证配置..."
if ! ~/.hermes/hermes-agent/venv/bin/python3 -c "
import sys
sys.path.insert(0, '$HERMES_DIR/hermes-agent')
from sidecar.config import load_config
cfg = load_config('$CONFIG_DEST')
assert cfg.cardkit.app_id == '$APP_ID'
assert cfg.cardkit.app_secret == '$APP_SECRET'
print('✓ 配置验证通过')
" 2>&1; then
    echo "⚠️ 配置验证失败，请手动检查 $CONFIG_DEST"
else
    echo "✓ 配置验证通过"
fi

# 完成
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  ✅ 配置完成！"
echo "═══════════════════════════════════════════════════════════════"
echo ""
echo "下一步："
echo "  1. 重启 Hermes Gateway："
echo "     hermes gateway restart"
echo ""
echo "  2. 启动 Sidecar（新终端）："
echo "     PYTHONPATH=~/.hermes/hermes-agent python3 -m sidecar.server"
echo ""
echo "  3. 在 Hermes 聊天中测试："
echo "     /model 选择 step-3.5-flash-2603 或 MiniMax-M2.7"
echo "     发送消息，查看流式卡片效果"
echo ""
echo "配置文件位置：$CONFIG_DEST"
echo ""
