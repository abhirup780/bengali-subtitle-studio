from __future__ import annotations
from ..openrouter import OpenRouterClient
from .base import LlmProvider
class OpenRouterLlmProvider(LlmProvider):
    def __init__(self, client: OpenRouterClient, model: str, temperature: float=0.2):
        self.client = client
        self.model = model
        self.temperature = temperature
    def complete(self, system: str, user: str) -> str:
        return self.client.chat(self.model, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}], temperature=self.temperature)
