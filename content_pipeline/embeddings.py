from typing import Protocol

import numpy as np

from content_pipeline.settings import PipelineSettings


class Embedder(Protocol):
    def __call__(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbedder:
    """Works with any OpenAI-compatible embeddings endpoint (OpenAI, Azure, vLLM, Ollama gateways)."""

    def __init__(self, settings: PipelineSettings):
        from openai import OpenAI

        self.client = OpenAI(base_url=settings.EMBEDDINGS_BASE_URL)
        self.model = settings.EMBEDDINGS_MODEL

    def __call__(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
