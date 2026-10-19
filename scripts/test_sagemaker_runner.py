"""Offline CPU or local-GPU validation of the SageMaker filesystem adapter. No AWS calls."""

import argparse
import subprocess
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CHECK = """
import json, os, runpy, subprocess, sys
from pathlib import Path
subprocess.run([sys.executable, '/fixture.py', '/opt/ml/input/data/bundle', 'fp16'], check=True)
sys.argv = ['/runner/sagemaker_entrypoint.py', '--image-digest',
            os.environ['TEST_IMAGE_DIGEST'], '--timeout', '180']
if os.environ['TEST_GPU'] == '0':
    sys.argv.append('--validate-only')
try:
    runpy.run_path('/runner/sagemaker_entrypoint.py', run_name='__main__')
except SystemExit as result:
    if result.code:
        for path in Path('/output').glob('*.log'):
            print(path.read_text()[-5000:])
        print(Path('/opt/ml/model/output-manifest.json').read_text())
    assert result.code == 0, 'managed runner failed'
assert os.getuid() == 10001 and os.getgid() == 10001 and os.getgroups() == []
manifest = json.loads(Path('/opt/ml/model/output-manifest.json').read_text())
assert manifest['status'] == ('succeeded' if os.environ['TEST_GPU'] == '1' else 'validated')
assert manifest['image_digest'] == os.environ['TEST_IMAGE_DIGEST']
assert not Path('/opt/ml/model/train.jsonl').exists()
assert not Path('/opt/ml/model/run.json').exists()
assert manifest['adapters'] or os.environ['TEST_GPU'] == '0'
print('Managed filesystem, privilege drop, offline Soup and bounded artifact publication passed')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--image", default="kitchensoup-sagemaker-trainer:local")
    args = parser.parse_args()
    image = subprocess.check_output(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True
    ).strip()
    name = "kitchensoup-sagemaker-check-" + uuid4().hex
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--pull",
                "never",
                "--network",
                "none",
                "--read-only",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "256",
                "--memory",
                "4g",
                "--shm-size",
                "256m",
                "--tmpfs",
                "/tmp:size=512m",
                "--tmpfs",
                "/opt/ml:size=1g",
                "--env",
                "HOME=/tmp",
                "--env",
                "OMP_NUM_THREADS=2",
                "--env",
                "TEST_IMAGE_DIGEST=" + image,
                "--env",
                "TEST_GPU=" + str(int(args.gpu)),
                "--volume",
                f"{ROOT / 'scripts/soup_smoke_fixture.py'}:/fixture.py:ro",
                "--volume",
                f"{ROOT / 'tests/fixtures/training/conversations.jsonl'}:/fixture.jsonl:ro",
                *(["--gpus", "all"] if args.gpu else []),
                "--entrypoint",
                "python",
                image,
                "-c",
                CHECK,
            ],
            check=True,
            timeout=300,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


if __name__ == "__main__":
    main()
