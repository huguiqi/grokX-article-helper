"""AI Provider 抽象基类"""

from abc import ABC, abstractmethod
from typing import List, Dict


class AIProvider(ABC):
    name: str

    @abstractmethod
    def chat_completion(self, api_key: str, messages: List[Dict],
                        model: str = None, temperature: float = 0.7) -> str:
        """调用聊天补全，返回文本"""

    def generate_image(self, api_key: str, prompt: str,
                       model: str = None, aspect_ratio: str = "16:9") -> str:
        """调用图片生成，返回 URL。不支持的 provider 抛 NotImplementedError"""
        raise NotImplementedError(f"{self.name} 不支持图片生成")

    def default_chat_model(self) -> str:
        return ""

    def default_image_model(self) -> str:
        return ""

    def base_url(self) -> str:
        return ""

    def get_effective_base_url(self) -> str:
        """返回实际使用的 base_url，AI_BASE_URL 环境变量优先"""
        from config import AI_BASE_URL
        return AI_BASE_URL or self.base_url()
