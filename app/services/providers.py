from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import LLMProvider as ProviderRecord
from app.db.session import unit_of_work
from app.providers.openai_compatible import LLMProvider
from app.providers.schemas import (
    ChatRequest,
    ProviderConfig,
    ProviderError,
    ProviderMessage,
    ProviderTestRequest,
    ProviderTestResult,
    ProviderView,
)


class TestAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]


class ProviderService:
    def __init__(
        self, factory: sessionmaker[Session], builder: Callable[[ProviderConfig], LLMProvider]
    ) -> None:
        self.factory = factory
        self.builder = builder

    @staticmethod
    def _row(session: Session, identifier: UUID) -> ProviderRecord:
        row = session.get(ProviderRecord, identifier)
        if row is None:
            raise ProviderError(404, "Provider not found")
        return row

    def list_providers(self) -> list[ProviderView]:
        with unit_of_work(self.factory) as session:
            return [
                ProviderView.model_validate(row)
                for row in session.scalars(
                    select(ProviderRecord).order_by(ProviderRecord.name, ProviderRecord.id)
                )
            ]

    def save(self, body: ProviderConfig, identifier: UUID | None = None) -> ProviderView:
        with unit_of_work(self.factory) as session:
            if identifier is None:
                row = ProviderRecord(**body.model_dump())
                session.add(row)
            else:
                row = self._row(session, identifier)
                for name, value in body.model_dump().items():
                    setattr(row, name, value)
            session.flush()
            result = ProviderView.model_validate(row)
            session.commit()
            return result

    def client(self, identifier: UUID) -> LLMProvider:
        with unit_of_work(self.factory) as session:
            row = self._row(session, identifier)
            config = ProviderConfig(
                name=row.name,
                base_url=row.base_url,
                api_key_ref=row.api_key_ref,
                model_names=list(row.model_names),
            )
        return self.builder(config)

    def test(self, identifier: UUID, body: ProviderTestRequest) -> ProviderTestResult:
        client = self.client(identifier)
        prompt = (
            'Return an object with status set to "ok".'
            if body.mode == "structured"
            else "This is a synthetic KitchenSoup connection test. Reply with OK."
        )
        request = ChatRequest(
            model=body.model,
            messages=[ProviderMessage(role="user", content=prompt)],
            max_completion_tokens=1024,
        )
        if body.mode == "structured":
            client.structured_output(request, TestAnswer)
        else:
            client.chat(request)
        # Provider-generated content is deliberately not echoed by the test endpoint.
        return ProviderTestResult(provider_id=identifier, model=body.model, mode=body.mode)
