"""App-owned embedding transport; no global clients or unconfigured network calls."""
import math

from openai import AsyncOpenAI


def normalized(vector):
    if not vector or len(vector) > 4096:
        raise ValueError("Invalid library embedding dimensions")
    values = [float(v) for v in vector]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Non-finite library embedding")
    norm = math.sqrt(math.fsum(v * v for v in values))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Empty library embedding")
    return [v / norm for v in values]


class LibraryEmbeddings:
    def __init__(self, *, api_key, model):
        self.fingerprint = f"openai:{model}"
        self.model = model
        self.client = AsyncOpenAI(api_key=api_key, timeout=30, max_retries=1)

    async def embed(self, texts):
        response = await self.client.embeddings.create(model=self.model, input=texts)
        items = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(texts))):
            raise ValueError("Embedding response omitted or duplicated an input")
        vectors = [normalized(item.embedding) for item in items]
        if len({len(v) for v in vectors}) != 1:
            raise ValueError("Embedding response has inconsistent dimensions")
        return vectors

    async def aclose(self):
        await self.client.close()
