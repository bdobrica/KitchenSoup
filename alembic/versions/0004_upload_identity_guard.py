"""Apply the existing identity/timestamp guard to upload reservations."""

from alembic import op

revision = "0004_upload_identity_guard"
down_revision = "876f49074a6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TRIGGER guard_metadata BEFORE UPDATE ON artifact_uploads "
        "FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_metadata()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_metadata ON artifact_uploads")
