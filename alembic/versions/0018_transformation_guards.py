"""Protect transformation identity, membership and state (hand-authored SQL)."""

from alembic import op

revision = "0018_transformation_guards"
down_revision = "0017_transformations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_transformation() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Transformation history is retained'; END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.status <> 'QUEUED' OR NEW.output_artifact_id IS NOT NULL THEN
          RAISE EXCEPTION 'Transformation must start queued without output';
        END IF;
        IF NEW.snapshot->>'schema' IS DISTINCT FROM 'kitchensoup.transformation/v1' OR
          NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.id=NEW.input_artifact_id AND
            a.model_version_id=NEW.model_version_id AND
            a.id::text=NEW.snapshot->>'artifact_id' AND
            a.sha256=NEW.snapshot->>'artifact_sha256' AND
            NEW.model_version_id::text=NEW.snapshot->>'model_version_id') THEN
          RAISE EXCEPTION 'Transformation input membership mismatch';
        END IF;
      ELSE
        IF (to_jsonb(NEW)-ARRAY['status','output_artifact_id','updated_at']) IS DISTINCT FROM
           (to_jsonb(OLD)-ARRAY['status','output_artifact_id','updated_at']) THEN
          RAISE EXCEPTION 'Transformation input is immutable';
        END IF;
        IF NEW.status <> OLD.status AND NOT (
          (OLD.status='QUEUED' AND NEW.status='PREPARING') OR
          (OLD.status='PREPARING' AND NEW.status IN ('RUNNING','POSTPROCESSING','FAILED','LOST')) OR
          (OLD.status='RUNNING' AND NEW.status IN ('POSTPROCESSING','FAILED','LOST')) OR
          (OLD.status='POSTPROCESSING' AND NEW.status IN ('SUCCEEDED','FAILED'))
        ) THEN RAISE EXCEPTION 'Invalid transformation transition'; END IF;
        IF OLD.output_artifact_id IS NOT NULL AND
          NEW.output_artifact_id IS DISTINCT FROM OLD.output_artifact_id THEN
          RAISE EXCEPTION 'Transformation output is immutable';
        END IF;
      END IF;
      IF NEW.output_artifact_id IS NOT NULL AND (
        NEW.status NOT IN ('POSTPROCESSING','SUCCEEDED') OR NOT EXISTS
          (SELECT 1 FROM artifacts a WHERE a.id=NEW.output_artifact_id AND
            a.model_version_id=NEW.model_version_id AND a.format=CASE
              WHEN NEW.snapshot->>'operation'='awq' THEN 'kitchensoup.awq-int4+zip/v1'
              ELSE 'kitchensoup.merged-'||(NEW.snapshot->>'precision')||'+zip/v1' END)
      ) THEN RAISE EXCEPTION 'Transformation output membership mismatch'; END IF;
      IF NEW.status='SUCCEEDED' AND NEW.output_artifact_id IS NULL THEN
        RAISE EXCEPTION 'Successful transformation requires registered output';
      END IF;
      RETURN NEW;
    END $$;
    CREATE TRIGGER guard_transformation BEFORE INSERT OR UPDATE OR DELETE
      ON artifact_transformations FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_transformation();
    CREATE TRIGGER guard_metadata BEFORE UPDATE ON artifact_transformations
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_metadata();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER guard_metadata ON artifact_transformations;
    DROP TRIGGER guard_transformation ON artifact_transformations;
    DROP FUNCTION kitchensoup_guard_transformation();
    """)
