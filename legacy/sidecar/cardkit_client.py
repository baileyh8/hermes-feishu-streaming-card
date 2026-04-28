"""
CardKitClient - 飞书 CardKit API 封装
================================================================

负责所有与飞书 CardKit 相关的 HTTP 调用：
  - 创建卡片
  - 更新卡片元素
  - 最终化卡片
  - 获取 tenant_access_token

设计原则：
  1. 自动重试：失败自动重试，最多 3 次
  2. 指数退避：重试间隔指数增长
  3. 令牌刷新：tenant_token 过期自动重新获取
  4. 请求合并：短时间内多个更新合并为一次请求
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiohttp

logger = logging.getLogger("sidecar.cardkit")


class CardKitClient:
    """CardKit API 客户端"""
    
    def __init__(self, config: Dict[str, Any], hermes_dir: str):
        """
        初始化客户端。
        
        Args:
            config: 完整配置字典
            hermes_dir: Hermes 目录（用于读取 app_id/app_secret）
        """
        self.config = config
        self.hermes_dir = hermes_dir
        
        # CardKit 配置
        cardkit_cfg = config.get('feishu_streaming_card', {}).get('cardkit', {})
        self.base_url = cardkit_cfg.get('base_url', 
            'https://open.feishu.cn/open-apis/cardkit/v1')
        self.timeout = cardkit_cfg.get('timeout', 30)
        self.max_retries = cardkit_cfg.get('max_retries', 3)
        self.retry_delay = cardkit_cfg.get('retry_delay', 1.0)
        
        # 飞书应用配置（从 .env 或 config 读取）
        self._app_id = config.get('feishu', {}).get('app_id')
        self._app_secret = config.get('feishu', {}).get('app_secret')
        
        if not self._app_id or not self._app_secret:
            # 尝试从 .env 读取
            env_path = Path(hermes_dir) / '.env'
            if env_path.exists():
                with open(env_path) as f:
                    for line in f:
                        if line.startswith('FEISHU_APP_ID='):
                            self._app_id = line.split('=', 1)[1].strip()
                        elif line.startswith('FEISHU_APP_SECRET='):
                            self._app_secret = line.split('=', 1)[1].strip()
        
        # Tenant token 缓存
        self._tenant_token: Optional[str] = None
        self._token_expires_at: datetime = datetime.min
        self._token_lock = asyncio.Lock()
        
        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None
        self._session_lock = asyncio.Lock()
        
        # 请求合并缓存（key -> (payload, timestamp)）
        self._pending_updates: Dict[str, tuple] = {}
        self._merge_lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """初始化（预获取 token，如果没有配置凭证则跳过）"""
        await self._ensure_session()
        if not self._app_id or not self._app_secret:
            logger.warning("[CardKit] App ID/Secret not configured — CardKit operations will fail")
            return
        try:
            await self._get_tenant_token(force=True)
        except Exception as e:
            logger.error(f"[CardKit] Token fetch failed: {e}")
            # 不抛出异常，允许 sidecar 在降级模式下运行
    
    async def shutdown(self) -> None:
        """清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _ensure_session(self) -> None:
        """确保 HTTP session 可用"""
        if self._session is None or self._session.closed:
            async with self._session_lock:
                if self._session is None or self._session.closed:
                    timeout = aiohttp.ClientTimeout(total=self.timeout)
                    self._session = aiohttp.ClientSession(timeout=timeout)
    
    async def _get_tenant_token(self, force: bool = False) -> str:
        """
        获取 tenant_access_token（带缓存）。
        
        Token 有效期 2 小时，提前 5 分钟刷新。
        """
        async with self._token_lock:
            now = datetime.now()
            
            # 检查缓存是否有效
            if not force and self._tenant_token and now < self._token_expires_at:
                return self._tenant_token
            
            # 调用 lark-cli 获取新 token
            logger.debug("[CardKit] Fetching new tenant token...")
            token = await self._fetch_token_via_lark_cli()
            
            if token:
                self._tenant_token = token
                # 缓存至 1 小时 55 分钟（留 5 分钟缓冲）
                self._token_expires_at = now + timedelta(minutes=115)
                logger.debug(f"[CardKit] Token refreshed, expires at {self._token_expires_at}")
                return token
            else:
                raise RuntimeError("Failed to fetch tenant_access_token")
    
    async def _fetch_token_via_lark_cli(self) -> Optional[str]:
        """通过 lark-cli 获取 token"""
        try:
            proc = await asyncio.create_subprocess_exec(
                'lark-cli', 'api', 'POST',
                '/open-apis/auth/v3/tenant_access_token/internal',
                '--data', json.dumps({
                    'app_id': self._app_id,
                    'app_secret': self._app_secret,
                }),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            
            if proc.returncode == 0:
                data = json.loads(stdout.decode())
                token = data.get('tenant_access_token')
                if token:
                    return token
                else:
                    logger.error(f"[CardKit] No token in response: {data}")
            else:
                logger.error(f"[CardKit] lark-cli error: {stderr.decode()}")
        except Exception as e:
            logger.error(f"[CardKit] Token fetch exception: {e}")
        
        return None
    
    async def _request(self, method: str, path: str, 
                      json_data: Optional[Dict] = None,
                      params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        发送 HTTP 请求（带重试）。
        """
        await self._ensure_session()
        token = await self._get_tenant_token()
        url = f"{self.base_url}{path}"
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json; charset=utf-8',
        }

        last_exc = None
        for attempt in range(self.max_retries):
            try:
                async with self._session.request(
                    method, url,
                    headers=headers,
                    json=json_data,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as resp:
                    resp_text = await resp.text()
                    
                    if resp.status == 200:
                        data = json.loads(resp_text)
                        if data.get('code') == 0:  # 飞书 API 成功
                            return data
                        else:
                            # API 业务错误
                            err_msg = data.get('msg', 'Unknown error')
                            logger.warning(f"[CardKit] API error: {err_msg}")
                            # 401 可能是 token 过期，强制刷新并重试
                            if resp.status == 401 and attempt < self.max_retries - 1:
                                await self._get_tenant_token(force=True)
                                continue
                            raise RuntimeError(f"CardKit API error: {err_msg}")
                    
                    elif resp.status in (429, 500, 502, 503, 504):
                        # 可重试的 HTTP 错误
                        logger.warning(f"[CardKit] HTTP {resp.status}, retrying...")
                        last_exc = RuntimeError(f"HTTP {resp.status}")
                    else:
                        # 不可重试的错误 — 保存调试信息
                        error_detail = f"HTTP {resp.status}: {resp_text}"
                        logger.error(f"[CardKit] {error_detail}")
                        try:
                            debug_path = Path('/tmp/cardkit_last_error.json')
                            with open(debug_path, 'w') as df:
                                import json as _json
                                _json.dump({
                                    'error': error_detail,
                                    'status': resp.status,
                                    'response': resp_text,
                                    'url': str(resp.url),
                                    'request_json': json_data,
                                }, df, indent=2, ensure_ascii=False)
                        except Exception:
                            pass
                        last_exc = RuntimeError(error_detail)
                        break
                    
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                logger.warning(f"[CardKit] Request failed (attempt {attempt+1}): {e}")
            
            # 检查是否是 card table number over limit 错误（不需要重试）
            if isinstance(last_exc, RuntimeError) and '11310' in str(last_exc):
                logger.warning(f"[CardKit] Card table limit reached, skipping retry")
                break
            
            # 等待重试（指数退避）
            if attempt < self.max_retries - 1:
                delay = self.retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
        
        # 所有重试失败
        raise last_exc or RuntimeError("Max retries exceeded")
    

    def _build_card(self, greeting: str, model: str,
                    user_input: str) -> Dict[str, Any]:
        """构建卡片 JSON 结构（CardKit v2 格式）。"""
        return {
            'schema': '2.0',
            'config': {
                'streaming_mode': True,
                'update_multi': True,
                'summary': {'content': '处理中...'},
                'streaming_config': {
                    'print_frequency_ms': {'default': 60, 'android': 60, 'ios': 60, 'pc': 60},
                    'print_step': {'default': 2, 'android': 2, 'ios': 2, 'pc': 2},
                    'print_strategy': 'fast',
                },
            },
            'header': {
                'template': 'indigo',
                'title': {'content': greeting, 'tag': 'plain_text'},
                'subtitle': {'content': '🤔 思考中...', 'tag': 'plain_text'},
            },
            'body': {
                'elements': [
                    {'tag': 'markdown', 'element_id': 'thinking_content',
                     'content': '⏳ 正在思考...'},
                ],
            },
        }

    def _build_updated_card(self, greeting: str, thinking_content: str,
                           tools: List[Dict] = None,
                           tool_count: int = 0,
                           tool_lines: List[str] = None) -> Dict[str, Any]:
        """构建更新后的卡片 JSON（用于 IM PATCH 更新）。"""
        # 构建工具状态行
        # 优先使用传入的 tool_count 和 tool_lines（从 thinking 内容检测）
        # 否则从 tools 列表计算
        if tool_count == 0 and tools:
            tool_count = len(tools)
        if tool_lines is None:
            tool_lines = []
            if tools:
                for t in tools:
                    status_emoji = {
                        'pending': '⏳',
                        'running': '🔄',
                        'completed': '✅',
                        'failed': '❌',
                    }.get(t.get('status', 'pending'), '•')
                    tool_lines.append(f"{status_emoji} `{t.get('name', '')}`")
        # 构建工具区块内容
        # 策略：过程中显示"执行中..."，完成后显示真实状态
        if tool_lines:
            # 检查是否有任何工具还在执行中
            any_running = any(t.get('status') in ('pending', 'running') for t in tools) if tools else False
            any_failed = any(t.get('status') == 'failed' for t in tools) if tools else False
            if any_failed:
                status_emoji = '❌'
            elif any_running:
                status_emoji = '⏳'
                _tool_section_content = '🔧 **执行中...**  ' + status_emoji
            else:
                status_emoji = '✅'
                _tool_section_content = f'🔧 **工具调用 ({tool_count}次)**  {status_emoji}\n\n' + '\n'.join([f'{t}' for t in tool_lines])
        elif tool_count > 0:
            _tool_section_content = f'🔧 **工具调用 ({tool_count}次)**'
        else:
            _tool_section_content = '🔧 **执行中...**  ⏳'
        # 过滤掉 markdown thinking block 标签
        full_content = thinking_content.replace('<think>', '').replace('</think>', '')
        if tool_lines:
            full_content += "\n\n" + "\n".join(tool_lines)

        return {
            'schema': '2.0',
            'config': {
                'streaming_mode': True,
                'update_multi': True,
                'summary': {'content': '处理中...'},
                'streaming_config': {
                    'print_frequency_ms': {'default': 60, 'android': 60, 'ios': 60, 'pc': 60},
                    'print_step': {'default': 2, 'android': 2, 'ios': 2, 'pc': 2},
                    'print_strategy': 'fast',
                },
            },
            'header': {
                'template': 'indigo',
                'title': {'content': greeting, 'tag': 'plain_text'},
                'subtitle': {'content': '🤔 思考中...', 'tag': 'plain_text'},
            },
            'body': {
                'elements': [
                    {'tag': 'markdown', 'element_id': 'thinking_content',
                     'content': full_content, 'margin': '0px 0px 6px 0px'},
                    {'tag': 'hr', 'element_id': 'divider', 'margin': '4px 0px'},
                    {'tag': 'markdown', 'element_id': 'tools_label',
                     'content': _tool_section_content, 'text_size': 'small',
                     'margin': '4px 0px 0px 0px'},
                ],
            },
        }

    def _build_footer(self, tokens: Dict[str, int], duration: float,
                       thinking_start: Optional[float] = None,
                       model: str = "minimax-M2.7") -> str:
        """构建 footer 文本内容（支持多种 token 格式）"""
        import time as _time
        now = _time.time()
        elapsed = int(duration) if duration else int((now - thinking_start) if thinking_start else 0)

        # 提取 token 数值（支持多种 key 格式和嵌套结构）
        def get_token_value(key_path: str) -> int:
            """从嵌套字典按路径提取 token 值"""
            keys = key_path.split('.')
            val = tokens
            for k in keys:
                if isinstance(val, dict):
                    val = val.get(k)
                else:
                    return 0
                if val is None:
                    return 0
            return int(val) if isinstance(val, (int, float, str)) else 0

        # 格式化数字（超过1000用k，超过1000000用m）
        def format_num(n: int) -> str:
            if n >= 1000000:
                return f"{n/1000000:.1f}m".rstrip('0').rstrip('.')
            elif n >= 1000:
                return f"{n/1000:.1f}k".rstrip('0').rstrip('.')
            return str(n)

        # 优先使用 last_prompt_tokens（实际发送到 API 的上下文大小）
        # 其次使用 input_tokens
        input_toks = (
            tokens.get('last_prompt_tokens') or
            get_token_value('usage.prompt_tokens') or
            get_token_value('usage.input_tokens') or
            tokens.get('input_tokens') or
            tokens.get('input') or
            tokens.get('prompt_tokens') or
            0
        )
        output_toks = (
            get_token_value('usage.completion_tokens') or
            get_token_value('usage.output_tokens') or
            tokens.get('output_tokens') or
            tokens.get('output') or
            tokens.get('completion_tokens') or
            0
        )
        cache_read = (
            get_token_value('usage.cache_read_tokens') or
            tokens.get('cache_read_tokens') or
            0
        )
        total_toks = input_toks + output_toks

        # 注意：input_tokens 来自 Hermes 返回值，可能是累计值而非当前上下文窗口使用量
        # 因此 ctx 百分比仅供参考，不能反映真实上下文使用情况
        ctx_window = 204800  # 假设 204k 上下文窗口

        return (
            f"{model}  ⏱️ {elapsed}s  {format_num(input_toks)}↑  {format_num(output_toks)}↓  "
            f"ctx {format_num(input_toks + cache_read)}/{format_num(ctx_window)}"
        )

    def _build_settings(self, config: Dict[str, Any]) -> str:
        """序列化卡片配置为 CardKit settings 字符串。"""
        settings = {'config': config}
        return json.dumps(settings, ensure_ascii=False)

    def _build_streaming_config(self) -> Dict[str, Any]:
        """构建流式配置。"""
        return {
            "print_frequency_ms": {"default": 70, "android": 70, "ios": 70, "pc": 70},
            "print_step": {"default": 1, "android": 1, "ios": 1, "pc": 1},
            "print_strategy": "fast",
        }

    async def create_card(self, chat_id: str, greeting: str,
                         model: str, user_input: str) -> tuple:
        """
        创建流式卡片。

        Args:
            chat_id: 聊天 ID
            greeting: 卡片标题
            model: 模型名称
            user_input: 用户输入（截断版）

        Returns:
            tuple: (card_id, message_id)
        """
        card_content = self._build_card(greeting, model, user_input)

        try:
            # 1. 通过 CardKit API 创建卡片（需要 type + data 包装）
            create_payload = {
                'type': 'card_json',
                'data': json.dumps(card_content, ensure_ascii=False),
            }
            result = await self._request('POST', '/cards', json_data=create_payload)
            card_id = result['data']['card_id']
            # CardKit 返回的 sequence 是创建后的下一个可用序列号
            returned_sequence = result.get('data', {}).get('sequence', 1)
            logger.info(f"[CardKit] Card created: {card_id} for chat {chat_id}, returned_sequence={returned_sequence}")

            # 2. 通过 IM API 发送卡片到聊天
            message_id = await self._send_card_message(chat_id, card_content)

            return card_id, message_id
        except Exception as e:
            logger.error(f"[CardKit] Create card failed: {e}")
            raise

    async def _patch_card_message(self, message_id: str, card_payload: Dict[str, Any]) -> None:
        """
        通过 IM API PATCH 更新已发送的卡片消息。

        Args:
            message_id: 消息 ID
            card_payload: 新的卡片内容
        """
        import json as _json

        msg_payload = {
            'content': _json.dumps(card_payload, ensure_ascii=False),
        }

        im_base_url = 'https://open.feishu.cn/open-apis/im/v1'

        try:
            await self._ensure_session()
            token = await self._get_tenant_token()
            url = f"{im_base_url}/messages/{message_id}"
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json; charset=utf-8',
            }

            async with self._session.patch(url, json=msg_payload, headers=headers) as resp:
                resp_text = await resp.text()
                if resp.status == 200:
                    data = _json.loads(resp_text)
                    if data.get('code') == 0:
                        logger.debug(f"[CardKit] Card message patched: {message_id}")
                        return
                    else:
                        raise RuntimeError(f"IM PATCH error: {data.get('msg')}")
                else:
                    raise RuntimeError(f"IM PATCH HTTP {resp.status}: {resp_text}")
        except Exception as e:
            logger.error(f"[CardKit] _patch_card_message error: {e}")
            raise

    async def update_card_message(self, message_id: str, greeting: str,
                                 content: str, tools: List[Dict] = None,
                                 tool_count: int = 0,
                                 tool_lines: List[str] = None) -> None:
        """
        通过 IM API PATCH 更新卡片消息。

        Args:
            message_id: 消息 ID
            greeting: 卡片标题
            content: 思考内容
            tools: 工具调用列表
            tool_count: 检测到的工具调用次数
            tool_lines: 检测到的工具名称列表
        """
        card_payload = self._build_updated_card(greeting, content, tools, tool_count, tool_lines)
        await self._patch_card_message(message_id, card_payload)

    async def finalize_card_message(self, message_id: str, final_content: str,
                                   tokens: Dict[str, int], duration: float,
                                   thinking_start: Optional[float] = None,
                                   api_calls: int = 0,
                                   tool_calls: List[str] = None,
                                   model: str = 'minimax-M2.7') -> None:
        """
        通过 IM API PATCH 最终化卡片消息。

        Args:
            message_id: 消息 ID
            final_content: 最终内容
            tokens: token 统计
            duration: 耗时
            thinking_start: 开始时间
            api_calls: API 调用次数
            tool_calls: 实际调用的工具名称列表
        """
        footer_text = self._build_footer(tokens, duration, thinking_start, model='minimax-M2.7')

        # 构建工具调用摘要（匹配旧版本格式）
        tool_elements = []
        if tool_calls:
            _unique_tools = list(dict.fromkeys(tool_calls))  # 去重保持顺序
            _count = len(_unique_tools)
            _tool_list = '\n'.join([f'⚙️ `{t}`' for t in _unique_tools])
            tool_elements.append({'tag': 'markdown', 'element_id': 'tool_summary',
                                  'content': f'🛠️ **工具调用 ({_count}次)**  ✅完成\n\n{_tool_list}'})
            tool_elements.append({'tag': 'hr', 'element_id': 'tool_divider'})
        elif api_calls > 0:
            tool_elements.append({'tag': 'markdown', 'element_id': 'tool_summary',
                                  'content': f'🛠️ **工具调用 ({api_calls}次)**  ✅完成'})
            tool_elements.append({'tag': 'hr', 'element_id': 'tool_divider'})

        final_card = {
            'schema': '2.0',
            'config': {
                'update_multi': True,
            },
            'header': {
                'template': 'green',
                'title': {'content': '✅ 回答完成', 'tag': 'plain_text'},
            },
            'body': {
                'elements': [
                    {'tag': 'markdown', 'element_id': 'final_content',
                     'content': final_content},
                    {'tag': 'hr', 'element_id': 'divider'},
                ] + tool_elements + [
                    {'tag': 'markdown', 'element_id': 'footer',
                     'content': footer_text, 'text_size': 'x-small'},
                ],
            },
        }

        logger.warning(f"[CardKit] Finalizing card message: message_id={message_id}, content_len={len(final_content)}")
        await self._patch_card_message(message_id, final_card)
        logger.warning(f"[CardKit] Card message finalized successfully")

    async def _send_card_message(self, chat_id: str, card_payload: Dict[str, Any]) -> str:
        """
        通过 IM API 发送卡片消息到聊天。

        Args:
            chat_id: 聊天 ID
            card_payload: 卡片内容（_build_card 返回的原始结构）

        Returns:
            message_id: 消息 ID
        """
        import json as _json

        # IM API 需要 content 是 JSON 字符串（双重序列化）
        msg_payload = {
            'receive_id': chat_id,
            'msg_type': 'interactive',
            'content': _json.dumps(card_payload, ensure_ascii=False),
        }

        # IM API 使用不同的 base URL
        im_base_url = 'https://open.feishu.cn/open-apis/im/v1'

        try:
            await self._ensure_session()
            token = await self._get_tenant_token()
            url = f"{im_base_url}/messages?receive_id_type=chat_id"
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json; charset=utf-8',
            }

            async with self._session.post(url, json=msg_payload, headers=headers) as resp:
                resp_text = await resp.text()
                if resp.status == 200:
                    data = _json.loads(resp_text)
                    if data.get('code') == 0:
                        message_id = data.get('data', {}).get('message_id')
                        logger.info(f"[CardKit] Card message sent: {message_id} to chat {chat_id}")
                        return message_id
                    else:
                        raise RuntimeError(f"IM API error: {data.get('msg')}")
                else:
                    raise RuntimeError(f"IM API HTTP {resp.status}: {resp_text}")
        except Exception as e:
            logger.error(f"[CardKit] Send card message failed: {e}")
            raise

    async def update_card(self, card_id: str, 
                         elements: List[Dict[str, Any]]) -> None:
        """
        更新卡片元素（PATCH）。
        """
        try:
            await self._request('PATCH', f'/cards/{card_id}', 
                               json_data={'elements': elements})
            logger.debug(f"[CardKit] Card updated: {card_id}")
        except Exception as e:
            logger.error(f"[CardKit] Update failed: {e}")
            raise
    
    async def finalize_card(self, card_id: str, final_content: str,
                           tokens: Dict[str, int], duration: float,
                           thinking_start: Optional[float] = None,
                           sequence: int = 1) -> None:
        """
        卡片最终化：使用全量更新接口替换卡片内容。
        """
        import uuid

        # 构建最终内容（含 footer 统计）
        footer_text = self._build_footer(tokens, duration, thinking_start, model='minimax-M2.7')
        full_content = f"{final_content}\n\n---\n{footer_text}"

        # 构建最终卡片内容
        final_card = {
            'schema': '2.0',
            'config': {
                'update_multi': True,
            },
            'header': {
                'template': 'indigo',
                'title': {'content': '回答完成', 'tag': 'plain_text'},
            },
            'body': {
                'elements': [
                    {'tag': 'markdown', 'element_id': 'thinking_content', 'content': full_content},
                ],
            },
        }

        try:
            # 使用全量更新 PUT /cards/:card_id
            await self._request(
                'PUT',
                f'/cards/{card_id}',
                json_data={
                    'card': {
                        'type': 'card_json',
                        'data': json.dumps(final_card, ensure_ascii=False),
                    },
                    'uuid': str(uuid.uuid4()),
                    'sequence': sequence,
                }
            )
            logger.info(f"[CardKit] Card finalized: {card_id}")
        except Exception as e:
            logger.error(f"[CardKit] Finalize failed: {e}")
            raise

    async def update_text_content(self, card_id: str, element_id: str,
                               content: str, sequence: int) -> None:
        """
        流式更新文本元素内容（打字机效果）。
        使用 PUT /cards/:card_id/elements/:element_id/content 接口。
        """
        import uuid
        try:
            await self._request(
                'PUT',
                f'/cards/{card_id}/elements/{element_id}/content',
                json_data={
                    'uuid': str(uuid.uuid4()),
                    'content': content,
                    'sequence': sequence,
                }
            )
            logger.debug(f"[CardKit] Text updated: {element_id} on card {card_id}")
        except Exception as e:
            logger.error(f"[CardKit] Update text failed: {e}")
            raise
