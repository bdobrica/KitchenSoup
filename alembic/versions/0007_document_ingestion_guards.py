"""Keep document ingestion snapshots immutable (hand-authored trigger)."""

from alembic import op

revision = "0007_document_ingestion_guards"
down_revision = "df16af97c9ad"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION kitchensoup_guard_document_ingestion() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'Document ingestion snapshots are immutable';
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER guard_document_ingestion BEFORE UPDATE ON document_ingestions
        FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_document_ingestion()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_document_ingestion ON document_ingestions")
    op.execute("DROP FUNCTION kitchensoup_guard_document_ingestion()")
