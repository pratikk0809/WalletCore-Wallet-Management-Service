"""Request/response shapes for the auth endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr = Field(examples=["alice@example.com"])
    # Min 8 for safety; max 128 so huge inputs can't be used to slow the server.
    password: str = Field(min_length=8, max_length=128, examples=["S3cret!pass"])


class UserResponse(BaseModel):
    """Never include password_hash here."""

    # from_attributes lets Pydantic read fields straight from a SQLAlchemy object.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
