#!/bin/bash
# 飞书应用凭证配置向导

set -e
HERMES_DIR="$HOME/.hermes"
ENV_FILE="$HERMES_DIR/.env"
EXAMPLE_FILE="$HERMES_DIR/.env.feishu.example"

echo "======================================"
echo " 飞书流式卡片 - 凭证配置向导"
echo "======================================"
echo ""
echo "步骤 1: 创建飞书开放平台应用"
echo "  1. 访问 https://open.feishu.cn/"
echo "  2. 创建应用 -> 选择'自建应用'"
echo "  3. 权限范围添加: im:chat:read, im:message:read, im:message:send, contact:member:read"
echo "  4. 在'凭证与基础信息'页面获取 App ID 和 App Secret"
echo ""
read -p "已创建应用？按回车继续..."

echo ""
read -p "App ID: " APP_ID
read -s -p "App Secret: " APP_SECRET
echo ""

if [ -z "$APP_ID" ] || [ -z "$APP_SECRET" ]; then
    echo "错误: App ID 和 App Secret 不能为空"
    exit 1
fi

if [ -f "$ENV_FILE" ]; then
    cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%Y%m%d_%H%M%S)"
    echo "✓ 已备份 .env"
fi

{
    echo ""
    echo "# Feishu credentials (setup_feishu.sh)"
    echo "export FEISHU_APP_ID=\"$APP_ID\""
    echo "export FEISHU_APP_SECRET=\"$APP_SECRET\""
} >> "$ENV_FILE"

echo "✓ 凭证已写入 $ENV_FILE"
echo ""
echo "下一步:"
echo "  source $ENV_FILE"
echo "  hermes sidecar check-env"
echo "  hermes sidecar start"
