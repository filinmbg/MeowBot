from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=255)
    timezone: str = "Europe/Kiev"
    language_code: str = "uk"


class UserResponse(BaseModel):
    id: UUID
    email: EmailStr | None
    status: str
    role: str
    timezone: str
    language_code: str
    display_name: str | None
    created_at: datetime
    updated_at: datetime