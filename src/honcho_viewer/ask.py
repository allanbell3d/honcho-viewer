"""Builds the JSON body for Honcho's dialectic /chat endpoint from the Ask form."""
from __future__ import annotations

import json


def build_chat_body(query: str, reasoning_level: str, include_evidence: bool, *,
                    session_id: str | None = None, target: str | None = None,
                    scope: str = "", response_format: str = "") -> tuple[dict, str | None]:
    """Return (body, error). ``error`` is a human-readable problem, or None if the body is valid."""
    body: dict = {"query": query.strip(), "reasoning_level": reasoning_level, "include_evidence": include_evidence}
    if session_id:
        body["session_id"] = session_id
    if target:
        body["target"] = target
    scopes = [s.strip() for s in scope.split(",") if s.strip()]
    if scopes:
        body["scope"] = scopes[0] if len(scopes) == 1 else scopes
    if response_format.strip():
        try:
            body["response_format"] = json.loads(response_format)
        except ValueError as exc:
            return body, f"Response format is not valid JSON: {exc}"
    if not body["query"]:
        return body, "Type a question first."
    if scopes and session_id:
        return body, "Use a session or a scope, not both."
    return body, None
