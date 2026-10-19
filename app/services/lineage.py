"""Independent, PostgreSQL-owned model and representation graphs."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    ArtifactDerivation,
    Model,
    ModelSource,
    ModelVersion,
    ModelVersionParent,
)
from app.registry.inspection import RegistryError
from app.registry.schemas import SourceView


class LineageNode(BaseModel):
    id: UUID
    label: str
    model_id: UUID | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    format: str | None = None
    sources: list[SourceView] = []


class LineageEdge(BaseModel):
    parent_id: UUID
    child_id: UUID
    operation: str


class LineageView(BaseModel):
    kind: Literal["model", "artifact"]
    root_id: UUID
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    mermaid: str


class BaseRequirement(BaseModel):
    model_version_id: UUID
    ready: bool
    requires_merge: bool
    artifact_id: UUID | None = None
    message: str


def base_requirement(session: Session, identifier: UUID) -> BaseRequirement:
    version = session.get(ModelVersion, identifier)
    if version is None:
        raise RegistryError(404, "Model version not found")
    sources = list(
        session.scalars(select(ModelSource).where(ModelSource.model_version_id == identifier))
    )
    derived = version.training_run_id is not None
    # A trained logical version is an adapter representation. Never interpret it
    # as a complete Transformers source or silently train against its ancestor.
    ready = not derived and len(sources) == 1 and not sources[0].gated
    return BaseRequirement(
        model_version_id=identifier,
        ready=ready,
        requires_merge=derived,
        artifact_id=sources[0].artifact_id if ready else None,
        message="A merged model representation is required before further training. "
        "Merge execution is planned for Milestone 17."
        if derived
        else "Registered base model is available."
        if ready
        else "Choose one ungated registered model source.",
    )


def graph(session: Session, identifier: UUID, kind: Literal["model", "artifact"]) -> LineageView:
    entity = ModelVersion if kind == "model" else Artifact
    if session.get(entity, identifier) is None:
        raise RegistryError(404, "Lineage root not found")
    # Follow ancestors only; sibling or unrelated metadata is not lineage.
    pending, seen, edges = [identifier], set(), []
    nodes = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if len(seen) > 1000:
            raise RegistryError(422, "Lineage graph exceeds the 1000-node display limit")
        if kind == "model":
            version = session.get(ModelVersion, current)
            assert version is not None
            model = session.get(Model, version.model_id)
            assert model is not None
            nodes.append(
                LineageNode(
                    id=current,
                    label=f"{model.name} v{version.version}",
                    model_id=model.id,
                    sources=[
                        SourceView.model_validate(source, from_attributes=True)
                        for source in session.scalars(
                            select(ModelSource)
                            .where(ModelSource.model_version_id == current)
                            .order_by(ModelSource.id)
                        )
                    ],
                )
            )
        else:
            artifact = session.get(Artifact, current)
            assert artifact is not None
            nodes.append(
                LineageNode(
                    id=current,
                    label=artifact.format,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    format=artifact.format,
                )
            )
        parent_edges: list[ModelVersionParent] | list[ArtifactDerivation]
        if kind == "model":
            parent_edges = list(
                session.scalars(
                    select(ModelVersionParent)
                    .where(ModelVersionParent.child_id == current)
                    .order_by(ModelVersionParent.parent_id)
                )
            )
        else:
            parent_edges = list(
                session.scalars(
                    select(ArtifactDerivation)
                    .where(ArtifactDerivation.child_id == current)
                    .order_by(ArtifactDerivation.parent_id)
                )
            )
        for edge in parent_edges:
            edges.append(
                LineageEdge(
                    parent_id=edge.parent_id,
                    child_id=current,
                    operation=edge.operation
                    if isinstance(edge, ArtifactDerivation)
                    else "fine-tuned-from",
                )
            )
            pending.append(edge.parent_id)
    nodes.sort(key=lambda node: str(node.id))
    edges.sort(key=lambda edge: (str(edge.parent_id), str(edge.child_id)))
    # UUID labels only in executable diagram text; user-controlled labels stay
    # in separately rendered text nodes, never Mermaid syntax/HTML.
    lines = ["flowchart LR"] + [f'  n{node.id.hex}["{node.id}"]' for node in nodes]
    lines += [f"  n{edge.parent_id.hex} --> n{edge.child_id.hex}" for edge in edges]
    return LineageView(
        kind=kind, root_id=identifier, nodes=nodes, edges=edges, mermaid="\n".join(lines)
    )
