"""Login, logout, the signed-in user and invite acceptance (CONTRACTS-V2 §3.1).

Placeholder created by V2-01 so **V2-02** can fill this file without touching
``main.py`` again: the router is already declared, exported and included by the
application factory. It carries no routes yet, which is why it contributes
nothing to the OpenAPI document.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1/auth", tags=["auth"])
