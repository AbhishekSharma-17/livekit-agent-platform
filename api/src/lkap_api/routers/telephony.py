"""SIP trunks, dispatch rules and phone numbers (stretch).

Placeholder created by V2-01 so **V2-17** can fill this file without touching
``main.py`` again: the router is already declared, exported and included by the
application factory. It carries no routes yet, which is why it contributes
nothing to the OpenAPI document.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1/telephony", tags=["telephony"])
