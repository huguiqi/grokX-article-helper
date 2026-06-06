"""Anthropic (Claude) Provider"""

import requests
from typing import List, Dict

from providers.base import AIProvider


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def base_url(self) -> str:
        return "https://api.anthropic.com"

    def default_chat_model(self) -> str:
        return "claude-sonnet-4-20250514"

    def chat_completion(self, api_key: str, messages: List[Dict],
                        model: str = None, temperature: float = 0.7) -> str:
        url = f"{self.base_url()}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        # Anthropic: system is a top-level field, not a message
        system_text = ""
        api_messages = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            else:
                api_messages.append(m)
        payload = {
            "model": model or self.default_chat_model(),
            "max_tokens": 8192,
            "messages": api_messages,
            "temperature": temperature,
        }
        if system_text:
            payload["system"] = system_text
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"Anthropic chat 错误 {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return data["content"][0]["text"]
