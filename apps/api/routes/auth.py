from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from apps.api.dependencies import get_current_user, get_learner_repo
from goalcoach.infrastructure.auth import create_access_token, hash_password, verify_password
from goalcoach.infrastructure.persistence.repositories import LearnerRepositoryError, UserAccount
from goalcoach.infrastructure.persistence.repositories import SqliteLearnerRepository

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class AuthUser(BaseModel):
    id: str
    email: str
    display_name: str
    learner_id: str
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUser


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=8, max_length=128)


def serialize_user(user: UserAccount) -> AuthUser:
    return AuthUser(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        learner_id=user.learner_id,
        created_at=user.created_at,
    )


def validate_email(email: str) -> str:
    normalized = email.strip().lower()
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    return normalized


@router.post("/register", response_model=AuthResponse)
async def register(
    payload: RegisterRequest,
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
) -> AuthResponse:
    email = validate_email(payload.email)
    password_hash, password_salt = hash_password(payload.password)
    try:
        user = await learner_repo.create_user(
            email=email,
            display_name=payload.display_name,
            password_hash=password_hash,
            password_salt=password_salt,
        )
    except LearnerRepositoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token = create_access_token(user.id)
    return AuthResponse(access_token=token, user=serialize_user(user))


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    learner_repo: SqliteLearnerRepository = Depends(get_learner_repo),
) -> AuthResponse:
    email = validate_email(payload.email)
    user_auth_record = await learner_repo.get_user_auth_record_by_email(email)
    if user_auth_record is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not verify_password(
        payload.password,
        password_hash=user_auth_record.password_hash,
        salt_hex=user_auth_record.password_salt,
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user_auth_record.account.id)
    return AuthResponse(access_token=token, user=serialize_user(user_auth_record.account))


@router.get("/me", response_model=AuthUser)
async def me(current_user: UserAccount = Depends(get_current_user)) -> AuthUser:
    return serialize_user(current_user)
