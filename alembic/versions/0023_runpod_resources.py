"""Runpod immutable targets and durable billable resource ownership."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0023_runpod_resources"
down_revision = "0022_sagemaker_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runpod_resources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("cleanup", sa.String(), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("pod_ids", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", postgresql.JSONB(), nullable=True),
        sa.Column("warning", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["target_id"], ["execution_targets.id"]),
        sa.UniqueConstraint("owner_id"),
        sa.CheckConstraint("kind IN ('training', 'deployment')", name="resource_kind"),
        sa.CheckConstraint("state IN ('INTENT', 'BOUND', 'RELEASED')", name="resource_state"),
        sa.CheckConstraint("cleanup IN ('NONE', 'PENDING', 'CONFIRMED')", name="cleanup_state"),
    )
    op.create_index("ix_runpod_resources_target_id", "runpod_resources", ["target_id"])
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_runpod_target() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.kind='runpod_pod' AND (NEW.configuration->>'schema' IS DISTINCT FROM
          'kitchensoup.runpod-target/v1' OR NEW.credential_ref IS NULL OR
          NEW.credential_ref !~ '^(env|file)://.+$') THEN
          RAISE EXCEPTION 'Runpod target requires versioned config and credential reference';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.kind='runpod_pod' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Runpod targets are immutable'; END IF;
        IF (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at') THEN
          RAISE EXCEPTION 'Runpod targets are immutable; create a new target';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.kind='runpod_pod' THEN
        RAISE EXCEPTION 'An existing target cannot become a Runpod target';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER runpod_target_guard BEFORE INSERT OR UPDATE OR DELETE ON execution_targets
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_runpod_target();
    CREATE FUNCTION kitchensoup_guard_runpod_resource() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Runpod ownership records cannot be deleted'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.state <> 'INTENT' OR NEW.cleanup <> 'NONE' OR NEW.pod_ids <> '[]'::jsonb
          OR NOT EXISTS (SELECT 1 FROM execution_targets
              WHERE id=NEW.target_id AND kind='runpod_pod')
          OR (NEW.kind='training' AND NOT EXISTS (SELECT 1 FROM training_runs
              WHERE id=NEW.owner_id AND execution_target_id=NEW.target_id))
          OR (NEW.kind='deployment' AND NOT EXISTS (SELECT 1 FROM deployments
              WHERE id=NEW.owner_id AND execution_target_id=NEW.target_id)) THEN
          RAISE EXCEPTION 'Invalid Runpod provisioning intent';
        END IF;
      ELSE
        IF (NEW.id,NEW.target_id,NEW.owner_id,NEW.kind,NEW.request,NEW.expires_at,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.target_id,OLD.owner_id,OLD.kind,OLD.request,OLD.expires_at,OLD.created_at)
          OR (OLD.state='RELEASED' AND NEW.state<>'RELEASED')
          OR NOT NEW.pod_ids @> OLD.pod_ids
          OR (OLD.outcome IS NOT NULL AND NEW.outcome IS DISTINCT FROM OLD.outcome)
          OR (NEW.state='RELEASED' AND NEW.cleanup<>'CONFIRMED') THEN
          RAISE EXCEPTION 'Runpod ownership and final outcomes are immutable';
        END IF;
        NEW.updated_at := clock_timestamp();
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER runpod_resource_guard BEFORE INSERT OR UPDATE OR DELETE ON runpod_resources
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_runpod_resource();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER runpod_resource_guard ON runpod_resources")
    op.execute("DROP FUNCTION kitchensoup_guard_runpod_resource()")
    op.execute("DROP TRIGGER runpod_target_guard ON execution_targets")
    op.execute("DROP FUNCTION kitchensoup_guard_runpod_target()")
    op.drop_table("runpod_resources")
