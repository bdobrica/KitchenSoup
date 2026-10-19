# Compare base and fine-tuned responses

Apply `make migrate` and rebuild with `make build-soup` before submitting new
runs with evaluation suites. Restart the host web, worker and reconciler processes
as described in [local training](local-training.md). Default Compose services do
not gain Docker access.

1. Open `/evaluations` and save a named suite of 3–10 simple test prompts, one per
   line (up to 4000 characters each). Use examples withheld from training if you
   want to assess generalization. Saved suites have no edit/delete action.
2. Open a saved training plan, select the optional evaluation suite, then start
   training. If you created the suite in another tab, reload the saved plan first.
   The suite is bound to that run before training; retries by clone retain it.
3. After training succeeds, follow **Compare base and fine-tuned responses** from
   the run, or **Evaluate this model** from the registered model page.
4. Choose **Generate / resume missing responses**. The independent host worker
   runs the base and adapter sequentially on the NVIDIA GPU. Refresh or leave the
   page open to poll durable progress. No external provider receives the prompts.
5. Compare both answers side by side and choose **Base better**, **Fine-tuned
   better** or **Same** per prompt. The summary also shows unreviewed prompts.
   Download the JSON report; the registered model page shows the current summary.

Evaluation consumes GPU time. Avoid training or other GPU workloads concurrently;
there is no capacity scheduler. Each answer can take up to ten minutes. A failed
attempt retains completed answers; fix the underlying image/context/GPU issue and
explicitly resume. A worker interruption becomes FAILED after the next worker
cleans up its identified container; uncertain work is not silently repeated.

## Generation and reports

The runtime loads the exact registered base and LoRA directly, without a merge or
vLLM deployment. It materializes registered inputs once per evaluation attempt,
then uses separate offline, restricted containers for each answer. Both responses
use greedy generation, one beam, at most 128 new tokens and the reviewed training
precision. Select FP16 in the plan for RTX 20 series; BF16 is optional and requires
native BF16 hardware. The full prompt plus generation allowance must fit the
reviewed maximum sequence length; overlong input fails rather than being truncated.
Build the updated image *before* creating evaluation-enabled training runs: their
image identity stays pinned and cannot be replaced by retagging an image later.

Each generated response has an artifact SHA-256 and an immutable run/prompt/model
association. Repeat evaluations reuse completed responses for that run and these
fixed settings. Preferences are human judgments, not automated scores. Reports
include the prompts, answers, preferences/counts, exact run/suite/model IDs, plan
hash, image and generation settings. Each preference creates a new hashed report
snapshot; earlier artifacts are preserved. Report derivations point to response
artifacts; tuned response derivations point to the adapter. All UI content is
rendered as text, including model-generated markup.

The [v1 OpenAPI](contracts/artifacts-v1.openapi.json) is additive; AppSpec, resolved
plan and training runner contracts are unchanged. New standalone schemas cover
[inference input](contracts/inference-input-v1.schema.json),
[inference output](contracts/inference-output-v1.schema.json) and
[evaluation reports](contracts/evaluation-report-v1.schema.json).

| Method and path under `/api/v1` | Behavior |
| --- | --- |
| POST `/evaluation-suites` | `{name, prompts: [string, …]}`, 201; new frozen suite |
| GET `/evaluation-suites` | List suites with ordered prompt IDs/text |
| GET `/evaluation-suites/{id}` | Read one suite |
| POST `/training-runs` | Existing `{plan_id}` gains optional `evaluation_suite_id`; old clients remain valid |
| POST `/evaluations` | `{training_run_id}`, 202; queue an attempt or reuse a pending one |
| GET `/evaluations?training_run_id={id}` | Attempts newest first, responses and current summary |
| GET `/evaluations/{id}` | Durable QUEUED/RUNNING/SUCCEEDED/FAILED status and response pairs |
| PUT `/evaluations/{id}/preferences/{prompt_id}` | `{preference: base_better\|fine_tuned_better\|same}`; update judgment/report |
| POST `/evaluations/{id}/report` | Export/reuse the current report snapshot after successful generation |

Views include `report_artifact_id`, downloadable through the existing artifact
API. Missing resources return 404, invalid inputs 422, incomplete/incompatible
runs or competing operations 409, disabled inference/unavailable services 503.
Responses are `Cache-Control: no-store`. Storage writes and PostgreSQL commits are
not atomic; interrupted writes can leave unregistered content-addressed objects.
Automatic retention, arbitrary generation parameters, automated benchmarks and
external-provider evaluation are outside this milestone.

## Verification

```sh
make verify
make test-integration
make test-evaluation-gpu PRECISION=fp16
```

The opt-in GPU smoke uses locally generated tiny random Qwen2 weights and an
untrained synthetic LoRA adapter, without model downloads or billable providers.
It exercises the actual restricted base and adapter inference containers; it does
not establish numerical quality or retrain a model. See
[Milestone 15 evidence](evidence/milestone-15.md) and
[ADR 0016](adr/0016-local-paired-evaluation.md).
