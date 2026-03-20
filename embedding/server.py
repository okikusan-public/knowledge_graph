from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import numpy as np

app = FastAPI()

print("Loading model: intfloat/multilingual-e5-base ...")
model = SentenceTransformer("intfloat/multilingual-e5-base")
print("Model loaded successfully.")


class EmbedRequest(BaseModel):
    inputs: str | list[str]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/embed")
def embed(req: EmbedRequest):
    texts = req.inputs if isinstance(req.inputs, list) else [req.inputs]
    vectors = model.encode(texts, normalize_embeddings=True)
    return {"embeddings": vectors.tolist(), "dimensions": vectors.shape[1]}
