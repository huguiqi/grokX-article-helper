"""OpenAI (ChatGPT) Provider"""

import requests
from typing import List, Dict

from providers.base import AIProvider


class ChatGPTProvider(AIProvider):
    name = "chatGPT"

    def base_url(self) -> str:
        return "https://api.openai.com/v1"

    def default_chat_model(self) -> str:
        return "gpt-4o"

    def default_image_model(self) -> str:
        return "dall-e-3"

    def chat_completion(self, api_key: str, messages: List[Dict],
                        model: str = None, temperature: float = 0.7) -> str:
        url = f"{self.base_url()}/chat/completions"
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
            raise RuntimeError(f"OpenAI chat 错误 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def generate_image(self, api_key: str, prompt: str,
                       model: str = None, aspect_ratio: str = "16:9") -> str:
        url = f"{self.base_url()}/images/generations"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        # DALL-E uses "size" not "aspect_ratio"
        size_map = {
            "16:9": "1792x1024",
            "4:3": "1024x1024",
            "1:1": "1024x1024",
        }
        payload = {
            "model": model or self.default_image_model(),
            "prompt": prompt,
            "size": size_map.get(aspect_ratio, "1792x1024"),
            "n": 1,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI image 错误 {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        if "data" in data and data["data"]:
            return data["data"][0].get("url") or data["data"][0].get("b64_json")
        raise RuntimeError(f"无法解析图片返回: {data}")
