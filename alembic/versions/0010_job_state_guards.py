"""Enforce normalized transitions and atomically retain every state event.

Hand-authored PostgreSQL guards; no relational metadata change.
"""

from alembic import op

revision = "0010_job_state_guards"
down_revision = "0009_training_plan_guards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing history is retained. A baseline records the currently known state;
    # past transitions cannot be reconstructed and are not fabricated.
    op.execute("""
        INSERT INTO job_events
            (id, training_run_id, sequence, status, message, created_at, updated_at)
        SELECT gen_random_uuid(), r.id,
               COALESCE((SELECT max(sequence) + 1 FROM job_events WHERE training_run_id=r.id), 0),
               r.status, 'Lifecycle enforcement baseline', clock_timestamp(), clock_timestamp()
        FROM training_runs r
        WHERE NOT EXISTS (
            SELECT 1 FROM job_events e WHERE e.training_run_id=r.id AND e.status=r.status
            AND e.sequence=(SELECT max(sequence) FROM job_events WHERE training_run_id=r.id)
        )
    """)
    op.execute("""
        CREATE FUNCTION kitchensoup_job_state() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE allowed text[];
        BEGIN
            IF NEW.status NOT IN ('QUEUED', 'PREPARING', 'SUBMITTED', 'RUNNING',
                'POSTPROCESSING', 'SUCCEEDED', 'FAILED', 'CANCEL_REQUESTED',
                'CANCELED', 'LOST') THEN
                RAISE EXCEPTION 'Invalid job state';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.status IS NOT DISTINCT FROM OLD.status THEN
                RETURN NEW;
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'QUEUED' THEN
                    RAISE EXCEPTION 'New jobs must be QUEUED';
                END IF;
            ELSE
                allowed := CASE OLD.status
                    WHEN 'QUEUED' THEN ARRAY['PREPARING', 'CANCELED']
                    WHEN 'PREPARING' THEN ARRAY['SUBMITTED', 'FAILED', 'CANCEL_REQUESTED']
                    WHEN 'SUBMITTED' THEN ARRAY['RUNNING', 'FAILED', 'CANCEL_REQUESTED', 'LOST']
                    WHEN 'RUNNING' THEN
                        ARRAY['POSTPROCESSING', 'FAILED', 'CANCEL_REQUESTED', 'LOST']
                    WHEN 'POSTPROCESSING' THEN ARRAY['SUCCEEDED', 'FAILED']
                    WHEN 'CANCEL_REQUESTED' THEN
                        ARRAY['CANCELED', 'POSTPROCESSING', 'FAILED', 'LOST']
                    ELSE ARRAY[]::text[] END;
                IF NOT NEW.status = ANY(allowed) THEN
                    RAISE EXCEPTION 'Invalid job transition';
                END IF;
            END IF;
            INSERT INTO job_events (id, training_run_id, sequence, status, created_at, updated_at)
            VALUES (gen_random_uuid(), NEW.id,
                COALESCE((SELECT max(sequence)+1 FROM job_events WHERE training_run_id=NEW.id), 0),
                NEW.status, clock_timestamp(), clock_timestamp());
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER job_state AFTER INSERT OR UPDATE ON training_runs
        FOR EACH ROW EXECUTE FUNCTION kitchensoup_job_state();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER job_state ON training_runs")
    op.execute("DROP FUNCTION kitchensoup_job_state()")
