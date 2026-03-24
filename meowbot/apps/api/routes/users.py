from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status

from meowbot.apps.api.schemas.users import CreateUserRequest, UserResponse
from meowbot.infra.postgres.repos.users_repo import UsersRepo


router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: CreateUserRequest, request: Request) -> UserResponse:
    repo = UsersRepo(request.app.state.pg)
    user = await repo.create_user(
        email=payload.email,
        display_name=payload.display_name,
        timezone=payload.timezone,
        language_code=payload.language_code,
    )
    return UserResponse(**user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: UUID, request: Request) -> UserResponse:
    repo = UsersRepo(request.app.state.pg)
    user = await repo.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(**user)