"""Durable, monotonic raw-object deletion requests (hand-authored DDL)."""

import sqlalchemy as sa

from alembic import op

revision = "0021_raw_source_deletions"
down_revision = "0020_kubernetes_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifact_uploads",
        sa.Column("staging_cleaned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "raw_source_deletions",
        sa.Column("artifact_id", sa.Uuid(), sa.ForeignKey("artifacts.id"), primary_key=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_raw_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Deletion requests are retained'; END IF;
      IF NEW.artifact_id IS DISTINCT FROM OLD.artifact_id OR
         NEW.requested_at IS DISTINCT FROM OLD.requested_at OR
         (OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS DISTINCT FROM OLD.deleted_at) THEN
        RAISE EXCEPTION 'Deletion requests are monotonic';
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER raw_deletion_guard BEFORE UPDATE OR DELETE ON raw_source_deletions
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_raw_deletion();
    """)


def downgrade() -> None:
    op.drop_column("artifact_uploads", "staging_cleaned_at")
    op.drop_table("raw_source_deletions")
    op.execute("DROP FUNCTION kitchensoup_guard_raw_deletion()")
