"""Protect identifiers, snapshots, and append-only event history.

This hand-authored SQL migration complements the generated relational schema.
"""

from alembic import op

revision = "0002_metadata_guards"
down_revision = "49a011c37db2"
branch_labels = None
depends_on = None

TABLES = (
    "model_catalog_entries",
    "models",
    "model_versions",
    "model_version_parents",
    "model_sources",
    "artifacts",
    "artifact_derivations",
    "datasets",
    "dataset_versions",
    "dataset_sources",
    "documents",
    "conversation_imports",
    "conversations",
    "training_runs",
    "job_events",
    "execution_targets",
    "llm_providers",
    "evaluation_suites",
    "evaluation_prompts",
    "evaluation_results",
    "deployments",
)


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION kitchensoup_guard_metadata() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id OR
               NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'Identifiers and creation timestamps are immutable';
            END IF;
            IF TG_TABLE_NAME IN ('dataset_versions', 'job_events') THEN
                IF NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'Snapshot and event records are immutable';
                END IF;
                RETURN OLD;
            END IF;
            IF TG_TABLE_NAME = 'training_runs' THEN
                IF (to_jsonb(NEW) - ARRAY['status', 'external_id', 'updated_at'])
                   IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['status', 'external_id', 'updated_at']) THEN
                    RAISE EXCEPTION 'Submitted run inputs are immutable';
                END IF;
            END IF;
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END;
        $$
    """)
    for table in TABLES:
        op.execute(
            f'CREATE TRIGGER guard_metadata BEFORE UPDATE ON "{table}" '
            "FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_metadata()"
        )
    op.execute("""
        CREATE FUNCTION kitchensoup_keep_job_events() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Job event history is immutable';
        END;
        $$
    """)
    op.execute(
        "CREATE TRIGGER keep_job_events BEFORE DELETE ON job_events "
        "FOR EACH ROW EXECUTE FUNCTION kitchensoup_keep_job_events()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER keep_job_events ON job_events")
    op.execute("DROP FUNCTION kitchensoup_keep_job_events()")
    for table in reversed(TABLES):
        op.execute(f'DROP TRIGGER guard_metadata ON "{table}"')
    op.execute("DROP FUNCTION kitchensoup_guard_metadata()")
