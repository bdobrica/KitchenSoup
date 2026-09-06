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
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Please check the model details.");
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
