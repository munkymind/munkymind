"""Embedding provider abstraction supporting OpenAI and Ollama."""
from __future__ import annotations
from typing import Any


class EmbeddingProvider:
    def __init__(self, provider: str, model: str, _client: Any = None):
        self._provider = provider
        self._model = model
        self._client = _client  # openai client or None for ollama

    @staticmethod
    def from_config(embed_config: dict) -> 'EmbeddingProvider':
        provider = embed_config.get('provider', 'openai')
        model = embed_config.get('model', 'text-embedding-3-small')

        if provider == 'openai':
            import openai
            client = openai.OpenAI(api_key=embed_config.get('api_key'))
            return EmbeddingProvider(provider='openai', model=model, _client=client)
        elif provider == 'ollama':
            return EmbeddingProvider(provider='ollama', model=model)
        else:
            raise ValueError(f"Unsupported embedding provider: {provider}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        if self._provider == 'openai':
            response = self._client.embeddings.create(model=self._model, input=texts)
            return [item.embedding for item in response.data]
        elif self._provider == 'ollama':
            import requests
            from mm.config.env import ollama_url
            embeddings = []
            for text in texts:
                resp = requests.post(
                    f'{ollama_url()}/api/embeddings',
                    json={'model': self._model, 'prompt': text},
                )
                resp.raise_for_status()
                embeddings.append(resp.json()['embedding'])
            return embeddings
        else:
            raise ValueError(f"Unsupported provider: {self._provider}")
