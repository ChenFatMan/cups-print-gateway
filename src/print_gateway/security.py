from __future__ import annotations

import hmac

from fastapi import Depends, Header, HTTPException, status

from .config import Settings, get_settings


def require_agent(
    x_agent_id: str = Header(..., alias="X-Agent-Id"),
    x_agent_token: str = Header(..., alias="X-Agent-Token"),
    settings: Settings = Depends(get_settings),
) -> str:
    if not hmac.compare_digest(x_agent_token, settings.agent_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid agent token")
    return x_agent_id
