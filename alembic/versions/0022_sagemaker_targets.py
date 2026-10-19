"""Freeze managed training authority behind immutable target IDs."""

from alembic import op

revision = "0022_sagemaker_targets"
down_revision = "0021_raw_source_deletions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_sagemaker_target() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.kind='sagemaker' AND (NEW.configuration->>'schema' IS DISTINCT FROM
           'kitchensoup.sagemaker-target/v1' OR NEW.credential_ref IS NULL OR
           NEW.credential_ref !~ '^aws-profile://[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}$') THEN
          RAISE EXCEPTION 'SageMaker target requires versioned config and profile reference';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.kind='sagemaker' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'SageMaker targets are immutable'; END IF;
        IF (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at') THEN
          RAISE EXCEPTION 'SageMaker targets are immutable; create a new target';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.kind='sagemaker' THEN
        RAISE EXCEPTION 'An existing target cannot become a SageMaker target';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER sagemaker_target_guard BEFORE INSERT OR UPDATE OR DELETE ON execution_targets
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_sagemaker_target();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER sagemaker_target_guard ON execution_targets")
    op.execute("DROP FUNCTION kitchensoup_guard_sagemaker_target()")
