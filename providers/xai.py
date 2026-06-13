"""xAI Provider (Grok)"""

import requests
from typing import List, Dict

from providers.base import AIProvider


class XAIProvider(AIProvider):
    name = "xAI"

    def base_url(self) -> str:
        return "https://api.x.ai/v1"

    def default_chat_model(self) -> str:
        return "grok-4.3"

    def default_image_model(self) -> str:
        return "grok-imagine-image-quality"

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
            raise RuntimeError(f"xAI chat 错误 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def generate_image(self, api_key: str, prompt: str,
                       model: str = None, aspect_ratio: str = "16:9") -> str:
        url = f"{self.get_effective_base_url()}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.default_image_model(),
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "n": 1,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"xAI image 错误 {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        if "data" in data and data["data"]:
            return data["data"][0].get("url") or data["data"][0].get("b64_json")
        if "url" in data:
            return data["url"]
        raise RuntimeError(f"无法解析图片返回: {data}")
