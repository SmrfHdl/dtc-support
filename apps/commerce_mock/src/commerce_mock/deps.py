"""FastAPI dependencies: DB session, the mock clock, the policy config."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dtc_policy import PolicyConfig


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    async with sessions() as session:
        yield session


def _now(request: Request) -> datetime:
    return request.app.state.settings.now


def _policy(request: Request) -> PolicyConfig:
    return request.app.state.policy


Session = Annotated[AsyncSession, Depends(_session)]
Now = Annotated[datetime, Depends(_now)]
Policy = Annotated[PolicyConfig, Depends(_policy)]
