"""Check the ML dependency closure; unrelated OS Python metadata is outside this image's seam."""

from importlib.metadata import distribution

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

pending = [Requirement(name) for name in ("autoawq", "peft", "datasets", "transformers", "torch")]
seen: set[str] = set()
while pending:
    requirement = pending.pop()
    if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
        continue
    package = distribution(requirement.name)
    if package.version not in requirement.specifier:
        raise SystemExit(f"Incompatible ML dependency: {requirement.name}")
    name = canonicalize_name(requirement.name)
    if name in seen:
        continue
    seen.add(name)
    pending.extend(Requirement(value) for value in package.requires or [])
print("Transformation ML dependency closure is consistent")
