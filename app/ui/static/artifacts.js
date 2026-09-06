const form = document.querySelector("#artifact-upload");
const statusText = document.querySelector("#upload-status");
const download = document.querySelector("#artifact-download");
let artifactId = null;

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) {
    throw new Error(typeof result.detail === "string" ? result.detail : "Please check the file details.");
  }
  return result;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.querySelector("#artifact-file").files[0];
  if (!file) return;
  const button = form.querySelector("button");
  button.disabled = true;
  download.hidden = true;
  document.querySelector("#artifact-hash").textContent = "";
  try {
    statusText.textContent = "Preparing upload…";
    const grant = await api("/api/v1/artifact-uploads", {
      filename: file.name, size_bytes: file.size,
      content_type: file.type || "application/octet-stream",
    });
    statusText.textContent = "Uploading…";
    const response = await fetch(grant.url, { method: "PUT", headers: grant.headers, body: file });
    if (!response.ok) throw new Error("Upload failed. Please try again.");
    statusText.textContent = "Verifying the uploaded file…";
    const artifact = await api(`/api/v1/artifact-uploads/${grant.upload_id}/complete`);
    artifactId = artifact.id;
    document.querySelector("#artifact-hash").textContent = `SHA-256: ${artifact.sha256}`;
    statusText.textContent = `Stored ${artifact.size_bytes} bytes successfully.`;
    download.hidden = false;
  } catch (error) {
    statusText.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

download.addEventListener("click", async () => {
  try {
    const result = await api(`/api/v1/artifacts/${artifactId}/download`);
    window.location.assign(result.url);
  } catch (error) {
    statusText.textContent = error.message;
  }
});
