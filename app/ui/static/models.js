const statusText = document.querySelector("#model-status");
function element(tag, text) {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}
async function api(path, body) {
  const response = await fetch(`/api/v1${path}`, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw window.kitchenSoupRequestError(result, "Please check the model details.");
  return result;
}
function openModel(model) { window.location.assign(`/models/${model.id}`); }
async function busy(button, task) {
  button.disabled = true;
  statusText.textContent = "Checking model…";
  try { await task(); } catch (error) { statusText.textContent = error.message; }
  finally { button.disabled = false; }
}
function licenseLink(source) {
  const line = element("p", `Source license: ${source.license}`);
  if (source.license_url && /^https?:\/\//.test(source.license_url)) {
    const link = element("a", " Read source license");
    link.href = source.license_url;
    link.rel = "noreferrer";
    line.append(link);
  }
  return line;
}
async function load() {
  const id = window.location.pathname.split("/")[2];
  if (id) {
    document.querySelector("#model-browse").hidden = true;
    const model = await api(`/models/${encodeURIComponent(id)}`);
    const detail = document.querySelector("#model-detail");
    detail.hidden = false;
    detail.append(element("h2", model.name));
    for (const version of model.versions) {
      detail.append(element("h3", `Version ${version.version}`));
      const playground = element('a', 'Launch temporary playground');
      playground.href = `/deployments?model_version_id=${version.id}`;
      detail.append(playground);
      const use = element("a", "Use as base model");
      use.href = `/training-plans?base_model_version_id=${version.id}`;
      detail.append(use);
      const requirement = await api(`/model-versions/${version.id}/base-requirement`);
      const requirementText = element("p", requirement.message);
      detail.append(requirementText);
      if (version.training_run_id) {
        const run = element("a", "Training run"); run.href = `/training-runs/${version.training_run_id}`; detail.append(run);
        const evaluation = element("a", "Evaluate this model");
        evaluation.href = `/evaluations?training_run_id=${version.training_run_id}`; detail.append(evaluation);
        const evaluations = await api(`/evaluations?training_run_id=${version.training_run_id}`);
        const completed = evaluations.find(item => item.status === 'SUCCEEDED');
        if (completed) detail.append(element('p', `Evaluation: base better ${completed.summary.base_better}, fine-tuned better ${completed.summary.fine_tuned_better}, same ${completed.summary.same}, unreviewed ${completed.summary.unreviewed}.`));
      }
      await window.showLineage(detail, `/api/v1/model-versions/${version.id}/lineage`, "Model lineage");
      const representations = element("section", "");
      detail.append(representations);
      const renderedArtifacts = new Set();
      const jobStatus = element("div", "");
      representations.append(element("h4", "Representations"), jobStatus);
      const refreshRepresentations = async () => {
        const jobs = await api(`/model-versions/${version.id}/transformations`);
        jobStatus.replaceChildren();
        for (const job of jobs) jobStatus.append(element("p", `${job.operation.toUpperCase()} ${job.precision} · ${job.status}`));
        const pending = jobs.some(job => !["SUCCEEDED", "FAILED", "LOST"].includes(job.status));
        for (const artifactId of version.artifact_ids) {
          if (renderedArtifacts.has(artifactId)) continue;
          const artifact = await api(`/artifacts/${artifactId}`);
          renderedArtifacts.add(artifactId);
          representations.append(element("p", `${artifact.format} · ${artifact.size_bytes.toLocaleString()} bytes · ${artifact.sha256}`));
          const artifactLink = element("a", "View artifact and derivation history");
          artifactLink.href = `/artifacts?artifact_id=${artifactId}`;
          representations.append(artifactLink);
          const merge = artifact.format === "kitchensoup.lora-adapter+zip/v1";
          const quantize = /^kitchensoup\.merged-(fp16|bf16)\+zip\/v1$/.test(artifact.format);
          if (!merge && !quantize) continue;
          const form = element("form", ""); form.dataset.artifact = artifactId;
          const select = element("select", "");
          for (const precision of merge ? ["fp16", "bf16"] : ["fp16"]) {
            const option = element("option", precision.toUpperCase()); option.value = precision; select.append(option);
          }
          const precisionLabel = element("label", "Precision "); precisionLabel.append(select); form.append(precisionLabel);
          const calibration = element("textarea", "");
          if (quantize) {
            calibration.required = true; calibration.maxLength = 65536;
            calibration.placeholder = "Calibration text: one sample per line (up to 128). Saved locally with this job; never sent externally.";
            const label = element("label", "AWQ calibration text"); label.append(calibration); form.append(label);
          }
          const button = element("button", merge ? "Create representation: merged" : "Create representation: AWQ INT4"); form.append(button);
          form.addEventListener("submit", event => {
            event.preventDefault(); busy(button, async () => {
              const body = {operation: merge ? "merge" : "awq", precision: select.value};
              if (quantize) body.calibration = calibration.value.split(/\r?\n/).filter(line => line.trim());
              await api(`/artifacts/${artifactId}/transformations`, body);
              statusText.textContent = "Transformation queued. Identical requests reuse the existing job.";
              await refreshRepresentations();
            });
          });
          representations.append(form);
        }
        if (pending) setTimeout(async () => {
          try {
            const updated = await api(`/models/${encodeURIComponent(id)}`);
            version.artifact_ids = updated.versions.find(item => item.id === version.id).artifact_ids;
            requirementText.textContent = (await api(`/model-versions/${version.id}/base-requirement`)).message;
            await refreshRepresentations();
          } catch (error) { statusText.textContent = error.message; }
        }, 5000);
      };
      await refreshRepresentations();
      for (const artifactId of version.artifact_ids) {
        const link = element("a", "View artifact and derivation history");
        link.href = `/artifacts?artifact_id=${artifactId}`; detail.append(link);
        await window.showLineage(detail, `/api/v1/artifacts/${artifactId}/lineage`, "Artifact lineage");
      }
      for (const source of version.sources) {
        detail.append(licenseLink(source), element("p", `Source: ${source.kind} — ${source.location}`),
          element("p", `Exact revision: ${source.revision || "Uploaded archive (identified by artifact SHA-256)"}`),
          element("p", `Gated: ${source.gated ? "yes" : "no"}`));
        if (source.artifact_id) {
          const artifact = await api(`/artifacts/${source.artifact_id}`);
          detail.append(element("p", `SHA-256: ${artifact.sha256}`));
        }
        if (source.notes) detail.append(element("p", source.notes));
      }
    }
    statusText.textContent = "Registered. Structural checks do not guarantee training or serving compatibility.";
    return;
  }
  const [catalog, models] = await Promise.all([api("/model-catalog"), api("/models")]);
  for (const entry of catalog) {
    const card = element("article", "");
    card.append(element("h3", entry.name), licenseLink(entry),
      element("p", `${entry.parameter_count.toLocaleString()} parameters · ${entry.context_length.toLocaleString()} token context · ${entry.architecture}`),
      element("p", `Source: ${entry.repository} at ${entry.revision}`),
      element("p", entry.vram_guidance), element("p", `Soup: ${entry.soup_compatibility}`),
      element("p", `vLLM: ${entry.vllm_compatibility}`),
      element("p", `Recipes: ${entry.supported_recipes.join(", ")}. Quantization: ${entry.quantization_support.join(", ")}`));
    const choose = element("button", "Choose model");
    choose.addEventListener("click", () => busy(choose, async () => openModel(await api(`/model-catalog/${entry.key}/register`, {}))));
    card.append(choose);
    document.querySelector("#catalog-list").append(card);
  }
  for (const model of models) {
    const link = element("a", model.name);
    link.href = `/models/${model.id}`;
    const item = element("li", ""); item.append(link);
    document.querySelector("#model-list").append(item);
  }
  if (!models.length) document.querySelector("#model-list").append(element("li", "No models registered yet."));
}
document.querySelector("#hf-import").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  busy(form.querySelector("button"), async () => openModel(await api("/model-imports/huggingface", Object.fromEntries(new FormData(form)))));
});
document.querySelector("#archive-import").addEventListener("submit", event => {
  event.preventDefault();
  const form = event.currentTarget;
  busy(form.querySelector("button"), async () => {
    const fields = new FormData(form), file = fields.get("file");
    statusText.textContent = "Uploading model archive…";
    const grant = await api("/artifact-uploads", {filename: file.name, size_bytes: file.size, content_type: "application/zip"});
    const uploaded = await fetch(grant.url, {method: "PUT", headers: grant.headers, body: file});
    if (!uploaded.ok) throw new Error("Archive upload failed. Please try again.");
    statusText.textContent = "Verifying archive contents…";
    const artifact = await api(`/artifact-uploads/${grant.upload_id}/complete`, {});
    fields.delete("file");
    const body = Object.fromEntries(fields);
    body.artifact_id = artifact.id;
    if (!body.license_url) delete body.license_url;
    openModel(await api("/model-imports/archive", body));
  });
});
load().catch(error => { statusText.textContent = error.message; });
