"""Knowledge connections (V5-20): bring-your-own vector stores and hosted re-rankers.

* :mod:`~lkap_api.knowledge_connections.settings` — each kind's non-secret settings.
* :mod:`~lkap_api.knowledge_connections.http` — the one guarded vendor call.
* :mod:`~lkap_api.knowledge_connections.runtime` — a row to a working store or re-ranker.
* :mod:`~lkap_api.knowledge_connections.service` and ``router`` — ``/v1/knowledge-connections``.

The stores live in ``lkap_api.kb.stores`` and the re-rankers in
``lkap_api.kb.rerankers``, beside the platform's own.
"""
