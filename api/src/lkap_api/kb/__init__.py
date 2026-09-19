"""Knowledge-base ingestion and retrieval: embeddings, vector store, chunking.

Everything in this package sits behind two Protocols (:class:`~lkap_api.kb.embed.Embedder`,
:class:`~lkap_api.kb.store.VectorStore`) so the concrete local implementations
(fastembed ONNX, LanceDB) stay swappable, per docs/ARCHITECTURE.md §D6.
"""

from __future__ import annotations
