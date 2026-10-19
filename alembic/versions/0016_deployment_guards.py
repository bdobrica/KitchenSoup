"""Protect serving snapshots and normalized lifecycle transitions (hand-authored SQL)."""

from alembic import op

revision = "0016_deployment_guards"
down_revision = "0015_deployment_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_deployment() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.snapshot->>'schema' IS DISTINCT FROM 'kitchensoup.deployment/v1' THEN
          RETURN NEW;
        END IF;
      ELSIF OLD.snapshot->>'schema' IS DISTINCT FROM 'kitchensoup.deployment/v1' AND
            NEW.snapshot->>'schema' IS DISTINCT FROM 'kitchensoup.deployment/v1' THEN
        RETURN NEW;
      END IF;
      IF TG_OP='UPDATE' THEN
        IF (to_jsonb(NEW)-ARRAY['status','endpoint','expires_at','keep_running','updated_at'])
           IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['status','endpoint','expires_at','keep_running','updated_at']) THEN
          RAISE EXCEPTION 'Deployment inputs are immutable';
        END IF;
      END IF;
      IF NEW.snapshot->>'schema' IS DISTINCT FROM 'kitchensoup.deployment/v1' THEN
        RETURN NEW;
      END IF;
      IF NEW.status NOT IN ('QUEUED','PREPARING','STARTING','RUNNING',
        'STOP_REQUESTED','STOPPED','FAILED','LOST') THEN
        RAISE EXCEPTION 'Invalid deployment state';
      END IF;
      IF TG_OP='INSERT' AND NEW.status <> 'QUEUED' THEN
        RAISE EXCEPTION 'Deployment must start QUEUED';
      END IF;
      IF TG_OP='UPDATE' AND NEW.status <> OLD.status AND NOT (
        (OLD.status='QUEUED' AND NEW.status IN ('PREPARING','STOP_REQUESTED')) OR
        (OLD.status='PREPARING' AND NEW.status IN
          ('STARTING','RUNNING','FAILED','STOP_REQUESTED')) OR
        (OLD.status='STARTING' AND NEW.status IN ('RUNNING','FAILED','LOST','STOP_REQUESTED')) OR
        (OLD.status='RUNNING' AND NEW.status IN ('STARTING','FAILED','LOST','STOP_REQUESTED')) OR
        (OLD.status='STOP_REQUESTED' AND NEW.status='STOPPED')
      ) THEN RAISE EXCEPTION 'Invalid deployment transition'; END IF;
      IF NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.id=NEW.artifact_id AND
        a.id::text=NEW.snapshot->>'artifact_id' AND a.sha256=NEW.snapshot->>'artifact_sha256' AND
        (a.model_version_id=NEW.model_version_id OR EXISTS
          (SELECT 1 FROM model_sources s WHERE s.model_version_id=NEW.model_version_id
           AND s.artifact_id=a.id))) THEN
        RAISE EXCEPTION 'Deployment artifact must belong to selected model';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_deployment BEFORE INSERT OR UPDATE ON deployments
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_deployment();
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER guard_deployment ON deployments; DROP FUNCTION kitchensoup_guard_deployment()"
    )
