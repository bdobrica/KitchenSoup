"""Freeze Kubernetes authority behind immutable target IDs."""

from alembic import op

revision = "0020_kubernetes_targets"
down_revision = "0019_ssh_target_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_kubernetes_target() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='INSERT' THEN
        IF NEW.kind='kubernetes' AND (NEW.configuration->>'schema' IS DISTINCT FROM
           'kitchensoup.kubernetes-target/v1' OR NEW.credential_ref IS NULL) THEN
          RAISE EXCEPTION 'Kubernetes target needs versioned config and credential reference';
        END IF;
        RETURN NEW;
      END IF;
      IF OLD.kind='kubernetes' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Kubernetes targets are immutable'; END IF;
        IF (to_jsonb(NEW)-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'updated_at') THEN
          RAISE EXCEPTION 'Kubernetes targets are immutable; create a new target';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.kind='kubernetes' THEN
        RAISE EXCEPTION 'An existing target cannot become a Kubernetes target';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER kubernetes_target_guard BEFORE INSERT OR UPDATE OR DELETE ON execution_targets
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_kubernetes_target();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER kubernetes_target_guard ON execution_targets")
    op.execute("DROP FUNCTION kitchensoup_guard_kubernetes_target()")
