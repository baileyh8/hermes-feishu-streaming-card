"""
Sidecar 配置加载与默认值
================================================================

配置层级（从低到高）：
  1. config.py 中的 DEFAULT_CONFIG
  2. 项目根目录 config.yaml.example
  3. ~/.hermes/feishu-sidecar.yaml
  4. 命令行参数（最高）
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Any

import yaml

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_CONFIG = {
    'server': {
        'host': 'localhost',
        'port': 8765,
        'health_path': '/health',
        'enable_metrics': True,
        'metrics_path': '/metrics',
    },
    'cardkit': {
        'base_url': 'https://open.feishu.cn/open-apis/cardkit/v1',
        'timeout': 30,
        'max_retries': 3,
        'retry_delay': 1.0,
    },
    'logging': {
        'level': 'INFO',
        'file': '',
        'max_bytes': 10485760,
        'backup_count': 5,
    },
    'card': {
        'merge_window_ms': 100,
        'max_age_seconds': 3600,
        'persistence': False,
    },
    'feishu_streaming_card': {
        'greeting': '主人，苏菲为您服务！',
    },
}


def load_config(config_path: str) -> Dict[str, Any]:
    """
    加载 sidecar 配置。
    
    搜索顺序：
      1. config_path 参数
      2. ~/.hermes/feishu-sidecar.yaml
      3. 项目根目录 config.yaml.example
      4. 默认配置
    
    Args:
        config_path: 配置文件路径（可能是相对路径）
        
    Returns:
        合并后的配置字典
    """
    # 深拷贝默认配置
    config = _deep_copy(DEFAULT_CONFIG)
    
    # 候选路径列表
    paths = [
        Path(config_path).expanduser(),
        Path.home() / '.hermes' / 'feishu-sidecar.yaml',
        Path(__file__).parent.parent / 'config.yaml.example',
    ]
    
    # 尝试加载第一个存在的文件
    loaded_from = None
    for path in paths:
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    user_config = yaml.safe_load(f) or {}
                try:
                    _deep_update(config, user_config)
                except Exception as e:
                    logger.error(f"[Sidecar] _deep_update failed: {e}")
                    raise
                loaded_from = str(path)
                logger.info(f"[Sidecar] DEBUG: user_config keys={list(user_config.keys())}, fsc={user_config.get('feishu_streaming_card', {})}")
                logger.info(f"Loaded sidecar config from {path}")
                logger.info(f"[Sidecar] DEBUG: user_config keys = {list(user_config.keys())}, feishu_streaming_card = {user_config.get('feishu_streaming_card', {})}")

                break
            except yaml.YAMLError as e:
                logger.error(f"Invalid YAML in {path}: {e}")
                continue
    
    if not loaded_from:
        logger.warning("No sidecar config file found, using defaults")
    
    # 环境变量覆盖
    _override_from_env(config)
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Hermes 配置兼容层：从已合并的 config 中提取 feishu_streaming_card.sidecar
    # ─────────────────────────────────────────────────────────────────────────────
    # config 已经过 _deep_update(config, user_config) 合并，直接从中提取
    fsc = config.get('feishu_streaming_card', {})
    if fsc:
        sidecar_cfg = fsc.get('sidecar', {})
        if sidecar_cfg:
            # 映射到 server
            if 'host' in sidecar_cfg or 'port' in sidecar_cfg:
                server_cfg = config.setdefault('server', {})
                if 'host' in sidecar_cfg:
                    server_cfg['host'] = sidecar_cfg['host']
                if 'port' in sidecar_cfg:
                    server_cfg['port'] = sidecar_cfg['port']
            # 映射到 cardkit (base_url, timeout)
            if 'base_url' in sidecar_cfg or 'timeout' in sidecar_cfg:
                cardkit_cfg = config.setdefault('cardkit', {})
                if 'base_url' in sidecar_cfg:
                    cardkit_cfg['base_url'] = sidecar_cfg['base_url']
                if 'timeout' in sidecar_cfg:
                    cardkit_cfg['timeout'] = sidecar_cfg['timeout']
            logger.info(f"[Sidecar] Config mapped: fsc_keys={list(fsc.keys())}, sidecar_keys={list(sidecar_cfg.keys())}")
    
    logger.info(f"[Sidecar] Before validate: config keys = {list(config.keys())}")
    # 确保关键键存在（即使映射失败也有默认值）
    config.setdefault('server', DEFAULT_CONFIG['server'])
    config.setdefault('cardkit', DEFAULT_CONFIG['cardkit'])
    # 类型转换和验证
    _validate_config(config)
    
    return config




def _deep_copy(obj: Any) -> Any:
    """深拷贝（简化版）"""
    if isinstance(obj, dict):
        return {k: _deep_copy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_copy(item) for item in obj]
    else:
        return obj


def _deep_update(base: Dict[str, Any], update: Dict[str, Any]) -> None:
    """递归更新字典"""
    for key, value in update.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def _override_from_env(config: Dict[str, Any]) -> None:
    """环境变量覆盖配置"""
    env_map = {
        'SIDECAR_HOST': ('server', 'host'),
        'SIDECAR_PORT': ('server', 'port', int),
        'SIDECAR_ENABLE_METRICS': ('server', 'enable_metrics', lambda v: v.lower() == 'true'),
        'SIDECAR_LOG_LEVEL': ('logging', 'level'),
        'SIDECAR_CARD_MERGE_MS': ('card', 'merge_window_ms', int),
        'SIDECAR_CARD_MAX_AGE': ('card', 'max_age_seconds', int),
    }
    
    for env_name, (section, key, *converter) in env_map.items():
        value = os.environ.get(env_name)
        if value is not None:
            # 类型转换
            if converter:
                converter_func = converter[0]
                try:
                    value = converter_func(value)
                except Exception as e:
                    logger.warning(f"Invalid env {env_name}={value}: {e}")
                    continue
            
            # 设置值
            if section not in config:
                config[section] = {}
            config[section][key] = value
            logger.debug(f"Config override from env: {env_name}={value}")


def _validate_config(config: Dict[str, Any]) -> None:
    """验证配置并打印警告"""
    # 端口范围检查
    port = config['server']['port']
    if not (1024 <= port <= 65535):
        logger.warning(f"Port {port} is outside recommended range (1024-65535)")
    
    # 日志级别检查
    valid_levels = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
    log_level = config['logging']['level'].upper()
    if log_level not in valid_levels:
        logger.warning(f"Invalid log level: {log_level}, defaulting to INFO")
        config['logging']['level'] = 'INFO'
    
    # CardKit URL 检查
    base_url = config['cardkit']['base_url']
    if not base_url.startswith(('http://', 'https://')):
        logger.warning(f"CardKit base_url should be absolute URL: {base_url}")


def save_config(config: Dict[str, Any], path: str) -> None:
    """
    保存配置到文件。
    
    Args:
        config: 配置字典
        path: 目标路径
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, default_flow_style=False, allow_unicode=True)
    
    logger.info(f"Config saved to {path}")
