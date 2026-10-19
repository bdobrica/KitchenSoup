"""Freeze configured SSH authority behind stable target IDs (hand-authored SQL)."""

from alembic import op

revision = "0019_ssh_target_guards"
down_revision = "0018_transformation_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_ssh_target() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.kind='ssh_docker' AND (NEW.configuration->>'schema' IS DISTINCT FROM
           'kitchensoup.ssh-target/v1' OR NEW.credential_ref IS NULL) THEN
          RAISE EXCEPTION 'SSH targets require a versioned configuration and credential reference';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.kind='ssh_docker' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'SSH targets are immutable'; END IF;
        IF (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at') THEN
          RAISE EXCEPTION 'SSH targets are immutable; create a new target';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.kind='ssh_docker' THEN
        RAISE EXCEPTION 'An existing target cannot become an SSH target';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER ssh_target_guard BEFORE INSERT OR UPDATE OR DELETE ON execution_targets
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_ssh_target();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER ssh_target_guard ON execution_targets")
    op.execute("DROP FUNCTION kitchensoup_guard_ssh_target()")
