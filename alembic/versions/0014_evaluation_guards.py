"""Preserve evaluation prompt snapshots and paired response identity."""

from alembic import op

revision = "0014_evaluation_guards"
down_revision = "0013_evaluation_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION kitchensoup_guard_evaluation() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE run training_runs; prompt evaluation_prompts;
    BEGIN
      IF TG_TABLE_NAME IN ('evaluation_suites','evaluation_prompts') THEN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'Evaluation suites and prompts are immutable';
        END IF;
        -- Serialize prompt insertion with submission freezing the suite.
        PERFORM 1 FROM evaluation_suites WHERE id=NEW.suite_id FOR UPDATE;
        IF EXISTS (SELECT 1 FROM training_runs WHERE evaluation_suite_id=NEW.suite_id) THEN
          RAISE EXCEPTION 'Submitted evaluation prompts are immutable';
        END IF;
        RETURN NEW;
      END IF;
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Evaluation response and task records are immutable';
      END IF;
      IF TG_OP='UPDATE' THEN
        IF TG_TABLE_NAME='evaluation_tasks' THEN
          IF NEW.training_run_id IS DISTINCT FROM OLD.training_run_id THEN
            RAISE EXCEPTION 'Evaluation run is immutable';
          END IF;
        ELSIF (to_jsonb(NEW)-ARRAY['preference','updated_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['preference','updated_at']) THEN
          RAISE EXCEPTION 'Evaluation responses are immutable';
        END IF;
      END IF;
      SELECT * INTO run FROM training_runs WHERE id=NEW.training_run_id;
      IF run.resolved_config->>'schema'='kitchensoup.local-run/v1' THEN
        IF run.status <> 'SUCCEEDED' OR run.evaluation_suite_id IS NULL THEN
          RAISE EXCEPTION 'Evaluation requires a successful run and preselected suite';
        END IF;
        IF TG_TABLE_NAME='evaluation_results' THEN
          SELECT * INTO prompt FROM evaluation_prompts WHERE id=NEW.prompt_id;
          IF prompt.suite_id IS DISTINCT FROM run.evaluation_suite_id THEN
            RAISE EXCEPTION 'Evaluation prompt suite mismatch';
          END IF;
          IF NEW.model_version_id <> run.base_model_version_id AND NOT EXISTS (
            SELECT 1 FROM model_versions WHERE id=NEW.model_version_id AND training_run_id=run.id
          ) THEN
            RAISE EXCEPTION 'Evaluation model mismatch';
          END IF;
          IF NEW.preference IS NOT NULL AND (
            NEW.preference NOT IN ('base_better','fine_tuned_better','same') OR
            NEW.model_version_id=run.base_model_version_id
          ) THEN RAISE EXCEPTION 'Invalid paired evaluation preference'; END IF;
        END IF;
      END IF;
      RETURN NEW;
    END; $$;
    CREATE TRIGGER evaluation_suite_snapshot BEFORE UPDATE OR DELETE ON evaluation_suites
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_evaluation();
    CREATE TRIGGER evaluation_prompt_snapshot BEFORE INSERT OR UPDATE OR DELETE
      ON evaluation_prompts
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_evaluation();
    CREATE TRIGGER evaluation_result_snapshot BEFORE INSERT OR UPDATE OR DELETE
      ON evaluation_results
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_evaluation();
    CREATE TRIGGER evaluation_task_snapshot BEFORE INSERT OR UPDATE OR DELETE ON evaluation_tasks
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_evaluation();
    CREATE TRIGGER guard_metadata BEFORE UPDATE ON evaluation_tasks
      FOR EACH ROW EXECUTE FUNCTION kitchensoup_guard_metadata();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER guard_metadata ON evaluation_tasks")
    for table, trigger in (
        ("evaluation_tasks", "evaluation_task_snapshot"),
        ("evaluation_results", "evaluation_result_snapshot"),
        ("evaluation_prompts", "evaluation_prompt_snapshot"),
        ("evaluation_suites", "evaluation_suite_snapshot"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    op.execute("DROP FUNCTION kitchensoup_guard_evaluation()")
