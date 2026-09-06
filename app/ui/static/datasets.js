const statusText = document.querySelector("#dataset-status");
const datasetId = window.location.pathname.split("/")[2];
function element(tag, text) {
  const node = document.createElement(tag); node.textContent = text; return node;
}
async function api(path, body, method) {
  const response = await fetch(`/api/v1${path}`, {
    method: method || (body === undefined ? "GET" : "POST"),
    headers: {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Please check the dataset or source details.");
  return result;
}
async function busy(button, task) {
  button.disabled = true;
  try { await task(); } catch (error) { statusText.textContent = error.message; }
  finally { button.disabled = false; }
}
async function download(path) {
  const grant = await api(path, {});
  window.location.assign(grant.url);
}
function optionalFields(form) {
  const fields = Object.fromEntries(new FormData(form));
  for (const key of Object.keys(fields)) if (fields[key] === "") delete fields[key];
  return fields;
}
async function refresh() {
  if (!datasetId) {
    const datasets = await api("/datasets");
    const list = document.querySelector("#dataset-list"); list.replaceChildren();
    for (const dataset of datasets) {
      const link = element("a", `${dataset.name} (${dataset.sources.length} sources)`);
      link.href = `/datasets/${dataset.id}`;
      const item = element("li", ""); item.append(link); list.append(item);
    }
    if (!datasets.length) list.append(element("li", "No datasets yet."));
    return;
  }
  document.querySelector("#dataset-browse").hidden = true;
  const dataset = await api(`/datasets/${encodeURIComponent(datasetId)}`);
  document.querySelector("#dataset-detail").hidden = false;
  document.querySelector("#dataset-title").textContent = dataset.name;
  document.querySelector("#dataset-description").textContent = dataset.description || "";
  document.querySelector("#dataset-license").textContent = `Dataset license: ${dataset.license || "Not specified"}`;
  const list = document.querySelector("#source-list"); list.replaceChildren();
  for (const source of dataset.sources) {
    const card = element("article", "");
    card.append(element("h3", source.filename), element("p", `${source.kind} · ${source.size_bytes.toLocaleString()} bytes · Added ${new Date(source.created_at).toLocaleString()}`),
      element("p", `Source license: ${source.license || "Not specified"}`), element("p", `SHA-256: ${source.sha256}`));
    for (const document of source.documents) card.append(element("p", `Document: ${document.title} · ${document.media_type} · ${document.canonical_artifact_id ? "Processed" : "Not processed yet"}`));
    const get = element("button", "Download original");
    get.addEventListener("click", () => busy(get, () => download(`/datasets/${datasetId}/sources/${source.id}/download`)));
    const remove = element("button", "Remove source");
    remove.addEventListener("click", () => {
      if (!window.confirm(`Remove ${source.filename} from this dataset? The original file will be retained.`)) return;
      busy(remove, async () => {
        const result = await api(`/datasets/${datasetId}/sources/${source.id}`, undefined, "DELETE");
        await refresh();
        statusText.textContent = "Source removed. The original file is retained.";
        const retained = element("button", "Download retained original");
        retained.addEventListener("click", () => busy(retained, () => download(`/artifacts/${result.artifact_id}/download`)));
        document.querySelector("#retained-source").replaceChildren(element("p", `Retained artifact: ${result.artifact_id}`), retained);
      });
    });
    card.append(get, remove);
    if (source.kind === "archive" && source.filename.toLowerCase().endsWith(".zip")) {
      const importButton = element("button", "Import ChatGPT conversations");
      importButton.addEventListener("click", () => busy(importButton, async () => {
        statusText.textContent = "Importing conversations…";
        const imported = await api(`/datasets/${datasetId}/sources/${source.id}/imports/chatgpt`, {});
        await refresh();
        statusText.textContent = "Conversations imported. Choose and save your selection below.";
        const canonical = element("button", "Download canonical conversations");
        canonical.addEventListener("click", () => busy(canonical, () => download(`/artifacts/${imported.canonical_artifact_id}/download`)));
        document.querySelector("#canonical-download").replaceChildren(canonical);
      }));
      card.append(importButton);
    }
    list.append(card);
  }
  if (!dataset.sources.length) list.append(element("p", "No sources yet."));
  document.dispatchEvent(new Event("dataset-refreshed"));
}
document.querySelector("#dataset-create").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget;
  busy(form.querySelector("button"), async () => {
    const dataset = await api("/datasets", optionalFields(form));
    window.location.assign(`/datasets/${dataset.id}`);
  });
});
document.querySelector("#source-upload").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget;
  busy(form.querySelector("button"), async () => {
    const fields = optionalFields(form), file = fields.file;
    statusText.textContent = "Uploading original file…";
    const grant = await api("/artifact-uploads", {filename: file.name, size_bytes: file.size, content_type: "application/octet-stream"});
    const uploaded = await fetch(grant.url, {method: "PUT", headers: grant.headers, body: file});
    if (!uploaded.ok) throw new Error("Source upload failed. Please try again.");
    const artifact = await api(`/artifact-uploads/${grant.upload_id}/complete`, {});
    statusText.textContent = "Checking source file…";
    delete fields.file; fields.artifact_id = artifact.id; fields.filename = file.name;
    await api(`/datasets/${datasetId}/sources`, fields);
    form.reset(); await refresh();
    statusText.textContent = "Source stored unchanged and ready for future processing.";
  });
});
refresh().catch(error => { statusText.textContent = error.message; });
