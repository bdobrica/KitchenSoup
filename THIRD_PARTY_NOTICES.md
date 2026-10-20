# Third-party components and notices

KitchenSoup's original code is distributed under [Apache-2.0](LICENSE), as declared
in `pyproject.toml`. Existing original source files do not require a mechanical
per-file header rewrite. Preserve any existing third-party copyright/license
headers when adapting code, and identify newly incorporated third-party source.
No third-party JavaScript source is vendored: the browser loads pinned HTMX and
Alpine.js from jsDelivr. Their upstream licenses apply.

| Component boundary | Source of version/license information |
| --- | --- |
| Application and development packages | `pyproject.toml`, installed distribution metadata and license files |
| Soup document/training engines | `images/soup-ingest/requirements.in`, `images/soup-trainer/requirements.in` and generated locks |
| vLLM and CUDA/PyTorch runtime images | Digest-pinned Dockerfiles under `images/vllm/`, upstream image/package license files |
| Merge/AWQ dependencies | `images/model-transform/requirements.in` and generated lock |
| PostgreSQL, Valkey, RustFS, Python base images | Pinned Compose/Dockerfile references and their upstream distribution notices |
| Models, datasets and derived artifacts | Exact source license/provenance recorded in the registry/manifests |

These references are an inventory, not a complete transitive SBOM or an assurance
that every distribution has the same license. Container images retain their base
filesystem and package notices; do not strip them. Redistributing an image or
wheel requires reviewing its actual dependency inventory and preserving applicable
license/notice files, including CUDA component terms. Model rights are separate;
see [model-license behavior](docs/model-licenses.md). No new third-party source
was copied into KitchenSoup for the documentation release.
