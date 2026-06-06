"""MiniMax Provider"""

import requests
from typing import List, Dict

from providers.base import AIProvider


class MiniMaxProvider(AIProvider):
    name = "miniMax"

    def base_url(self) -> str:
        return "https://api.minimaxi.com"

    def default_chat_model(self) -> str:
        return "MiniMax-M3"

    def default_image_model(self) -> str:
        return "image-01"

    def chat_completion(self, api_key: str, messages: List[Dict],
                        model: str = None, temperature: float = 0.7) -> str:
        url = f"{self.base_url()}/v1/chat/completions"
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
            raise RuntimeError(f"MiniMax chat 错误 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def generate_image(self, api_key: str, prompt: str,
                       model: str = None, aspect_ratio: str = "16:9") -> str:
        url = f"{self.base_url()}/v1/image_generation"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # MiniMax uses "aspect_ratio" in a different format
        ar_map = {
            "16:9": "16:9",
            "4:3": "4:3",
            "1:1": "1:1",
        }
        payload = {
            "model": model or self.default_image_model(),
            "prompt": prompt,
            "aspect_ratio": ar_map.get(aspect_ratio, "16:9"),
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"MiniMax image 错误 {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        if "data" in data and "image_urls" in data["data"] and data["data"]["image_urls"]:
            return data["data"]["image_urls"][0]
        if "data" in data and data["data"]:
            return data["data"][0].get("url", "")
        raise RuntimeError(f"无法解析图片返回: {data}")
