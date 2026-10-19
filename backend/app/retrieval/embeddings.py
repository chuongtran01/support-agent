"""Turn text into vectors. The OpenAI client sits behind a one-method interface."""

from typing import Protocol

from openai import AsyncOpenAI

from app.config import settings

BATCH_SIZE = 100


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...


class OpenAIEmbedder:
    def __init__(self, api_key: str = "", model: str = "", batch_size: int = BATCH_SIZE):
        api_key = api_key or settings.openai_api_key
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set; add it to .env")
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model or settings.embedding_model
        self._batch_size = batch_size

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            response = await self._client.embeddings.create(model=self._model, input=batch)
            vectors.extend(item.embedding for item in response.data)
        return vectors
