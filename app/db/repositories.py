"""Initial model registry persistence, scoped to a caller-owned transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Model


class ModelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, name: str, description: str | None = None) -> Model:
        model = Model(name=name, description=description)
        self.session.add(model)
        self.session.flush()
        return model

    def get(self, model_id: UUID) -> Model | None:
        return self.session.get(Model, model_id)

    def list(self) -> list[Model]:
        return list(self.session.scalars(select(Model).order_by(Model.created_at, Model.id)))

    def delete(self, model: Model) -> None:
        self.session.delete(model)
