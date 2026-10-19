"""Keep derivation edges acyclic and trained model identity immutable.

Hand-authored PostgreSQL guards, separate from generated relational changes.
"""

from alembic import op

revision = "0012_lineage_guards"
down_revision = "0011_training_outputs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION kitchensoup_guard_lineage() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE cyclic boolean;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Lineage edges are immutable';
            END IF;
            -- Serialize graph writes, including independent application writers.
            PERFORM pg_advisory_xact_lock(7140014);
            EXECUTE format('WITH RECURSIVE ancestors(id) AS (
                SELECT $1::uuid UNION
                SELECT e.parent_id FROM %I e JOIN ancestors a ON e.child_id = a.id
            ) SELECT EXISTS(SELECT 1 FROM ancestors WHERE id = $2)', TG_TABLE_NAME)
            INTO cyclic USING NEW.parent_id, NEW.child_id;
            IF cyclic THEN
                RAISE EXCEPTION 'Lineage cycle is forbidden';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE FUNCTION kitchensoup_guard_trained_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.training_run_id IS DISTINCT FROM OLD.training_run_id OR
               (OLD.training_run_id IS NOT NULL AND
                (NEW.model_id IS DISTINCT FROM OLD.model_id OR
                 NEW.version IS DISTINCT FROM OLD.version)) THEN
                RAISE EXCEPTION 'Trained model identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER guard_trained_version BEFORE UPDATE ON model_versions
        FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_trained_version();
    """)
    for table in ("model_version_parents", "artifact_derivations"):
        op.execute(
            f"CREATE TRIGGER guard_lineage BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_lineage()"
        )


def downgrade() -> None:
    for table in ("model_version_parents", "artifact_derivations"):
        op.execute(f"DROP TRIGGER guard_lineage ON {table}")
    op.execute("DROP TRIGGER guard_trained_version ON model_versions")
    op.execute("DROP FUNCTION kitchensoup_guard_trained_version()")
    op.execute("DROP FUNCTION kitchensoup_guard_lineage()")
