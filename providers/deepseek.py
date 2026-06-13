"""DeepSeek Provider"""

import requests
from typing import List, Dict

from providers.base import AIProvider


class DeepSeekProvider(AIProvider):
    name = "deepSeek"

    def base_url(self) -> str:
        return "https://api.deepseek.com/v1"

    def default_chat_model(self) -> str:
        return "deepseek-chat"

    def chat_completion(self, api_key: str, messages: List[Dict],
                        model: str = None, temperature: float = 0.7) -> str:
        url = f"{self.get_effective_base_url()}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.default_chat_model(),
            "messages": messages,
            "temperature": temperature,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek chat 错误 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]
