"""AI Provider 工厂"""

import os
from typing import Optional

from providers.base import AIProvider
from providers.xai import XAIProvider
from providers.chatgpt import ChatGPTProvider
from providers.deepseek import DeepSeekProvider
from providers.anthropic import AnthropicProvider
from providers.minimax import MiniMaxProvider

_PROVIDERS = {
    "xAI": XAIProvider,
    "chatGPT": ChatGPTProvider,
    "deepSeek": DeepSeekProvider,
    "anthropic": AnthropicProvider,
    "miniMax": MiniMaxProvider,
}

SUPPORTED_PROVIDERS = list(_PROVIDERS.keys())


def get_provider(name: Optional[str] = None) -> AIProvider:
    name = name or os.getenv("AI_PROVIDER", "xAI")
    cls = _PROVIDERS.get(name)
    if not cls:
        raise ValueError(f"未知 provider: {name}，支持: {SUPPORTED_PROVIDERS}")
    return cls()
