"""Authentication and company-scope dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.database.session import get_db
from backend.app.modules.identity.service import AccessContext, has_permission, resolve_access

bearer = HTTPBearer(auto_error=False)


def current_access(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    company_id: Annotated[int, Header(alias="X-Company-ID")],
    session: Annotated[Session, Depends(get_db)],
) -> AccessContext:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    context = resolve_access(session, credentials.credentials, company_id)
    if context is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active access to this company")
    return context


def require_permission(code: str):
    def dependency(context: Annotated[AccessContext, Depends(current_access)]) -> AccessContext:
        if not has_permission(context, code):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")
        return context

    return dependency
