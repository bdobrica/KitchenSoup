"""Keep reviewed training plans immutable (hand-authored trigger)."""

from alembic import op

revision = "0009_training_plan_guards"
down_revision = "f6263d0b8ecb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION kitchensoup_guard_training_plan() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW IS DISTINCT FROM OLD THEN
                RAISE EXCEPTION 'Training plan snapshots are immutable';
            END IF;
            RETURN OLD;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER guard_training_plan BEFORE UPDATE ON training_plans
        FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_training_plan()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_training_plan ON training_plans")
    op.execute("DROP FUNCTION kitchensoup_guard_training_plan()")
