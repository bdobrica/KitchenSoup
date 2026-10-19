"""Opt-in FP16 Kubernetes Job, verified output, cancellation and cleanup smoke."""

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import UUID, uuid4

from app.executors.kubernetes import KubernetesTrainingExecutor
from app.targets.kubernetes import KubernetesTransport
from app.targets.kubernetes_schemas import KubernetesConfig
from app.training.schemas import PlanPreview
from app.training.soup import RunnerInput

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig-ref", required=True)
    parser.add_argument("--secret-directory", type=Path, required=True)
    parser.add_argument("--namespace", default="kitchensoup-m19-validation")
    parser.add_argument(
        "--image", required=True, help="Cluster image repository@sha256:manifest-digest"
    )
    parser.add_argument("--fixture-image", default="kitchensoup-soup-trainer:local")
    args = parser.parse_args()
    target = KubernetesConfig(namespace=args.namespace, training_image=args.image, workspace_gib=2)
    transport = KubernetesTransport(target, args.kubeconfig_ref, args.secret_directory, set())
    with tempfile.TemporaryDirectory(prefix="kitchensoup-kubernetes-smoke-") as temporary:
        root = Path(temporary)
        bundle = root / "bundle"
        bundle.mkdir()
        fixture = root / "fixture.py"
        fixture.write_text((ROOT / "scripts/soup_smoke_fixture.py").read_text())
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "4g",
                "--tmpfs",
                "/tmp:size=512m",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--env",
                "HOME=/tmp",
                "--env",
                "OMP_NUM_THREADS=2",
                "--volume",
                f"{bundle}:/bundle",
                "--volume",
                f"{fixture}:/fixture.py:ro",
                "--volume",
                f"{ROOT / 'tests/fixtures/training/conversations.jsonl'}:/fixture.jsonl:ro",
                "--entrypoint",
                "python",
                args.fixture_image,
                "/fixture.py",
                "/bundle",
                "fp16",
            ],
            check=True,
            timeout=120,
        )
        run = RunnerInput.model_validate_json((bundle / "run.json").read_bytes())

        class Materializer:
            def materialize(self, identifier: UUID, plan: PlanPreview, destination: Path) -> None:
                assert plan == run.plan
                shutil.copytree(bundle, destination, dirs_exist_ok=True)
                (destination / "run.json").write_text(
                    run.model_copy(update={"run_id": identifier}).model_dump_json(by_alias=True)
                )

        executor = KubernetesTrainingExecutor(root / "staging", Materializer(), transport)
        executor.memory_gib, executor.cpus, executor.timeout = 4, 2, 240
        image = executor.image_identity()
        names: list[str] = []
        try:
            identifier = uuid4()
            # Record deterministic identity before a possibly ambiguous API call.
            from app.executors.local_docker import container_name

            names.append(container_name(identifier))
            name = executor.submit(identifier, run.plan, image)
            print("Kubernetes Job submitted; actual FP16 Soup training", flush=True)
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                observation = executor.status(name, image)
                if observation.state not in ("RUNNING", "PREPARING"):
                    break
                time.sleep(2)
            if observation.state != "SUCCEEDED":
                print(executor.logs(name, "stderr", 0).text[-3000:])
                raise RuntimeError(str(observation))
            # Recreate the executor to prove retained launch identity survives restarts.
            restarted = KubernetesTrainingExecutor(executor.root, Materializer(), transport)
            assert restarted.status(name, image).state == "SUCCEEDED"
            assert restarted.logs(name, "stdout", 0).cursor > 0
            with restarted.output(name, image) as output:
                size = len(output.read())
            restarted.cleanup(name, image)
            assert restarted.logs(name, "stdout", 0).cursor > 0
            print(
                json.dumps(
                    {
                        "precision": "fp16",
                        "image": image,
                        "verified_adapter_zip_bytes": size,
                        "restart": True,
                    }
                ),
                flush=True,
            )
            cancel_id = uuid4()
            names.append(container_name(cancel_id))
            canceled = executor.submit(cancel_id, run.plan, image)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                pods = transport.call(
                    transport.core.list_namespaced_pod,
                    label_selector="kitchensoup.run=" + str(cancel_id),
                )["items"]
                if any(
                    p.get("status", {}).get("phase") == "Running"
                    and p["metadata"]["name"] != canceled + "-io"
                    for p in pods
                ):
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Second GPU Job did not start before cancellation")
            assert executor.cancel(canceled, image).state == "CANCELED"
            executor.cleanup(canceled, image)
            print("Actual GPU Job cancellation and complete resource cleanup passed", flush=True)
        finally:
            for name in names:
                try:
                    executor.cancel(name, image)
                    executor.cleanup(name, image)
                except Exception:
                    print("Cleanup needs operator inspection for " + name, flush=True)


if __name__ == "__main__":
    main()
