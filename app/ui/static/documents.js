(() => {
  if (!datasetId) return;
  const message = document.querySelector("#document-status");
  const selected = new Set();
  async function loadDocuments() {
    const dataset = await api(`/datasets/${datasetId}`);
    const options = document.querySelector("#document-options"); options.replaceChildren();
    for (const source of dataset.sources.filter(source => /\.(pdf|docx|md|txt)$/i.test(source.filename))) {
      const label = element("label", "");
      const box = document.createElement("input"); box.type = "checkbox"; box.checked = selected.has(source.id);
      box.addEventListener("change", () => box.checked ? selected.add(source.id) : selected.delete(source.id));
      label.append(box, document.createTextNode(source.filename)); options.append(label);
    }
    const history = await api(`/datasets/${datasetId}/document-ingestions`);
    const list = document.querySelector("#document-history"); list.replaceChildren();
    for (const item of history) {
      const card = element("article", "");
      card.append(element("h3", `Document extraction ${item.version}: ${item.status}`),
        element("p", `${item.manifest.example_count} text examples · Soup ${item.manifest.soup_version}`));
      for (const source of item.manifest.sources) {
        card.append(element("p", `${source.filename}: ${source.disposition} · ${source.example_count} examples`));
        const warnings = {unsupported_document_type: "File type skipped", logs_truncated: "Logs were truncated",
          soup_stderr: "Soup wrote diagnostic output; see logs", pdf_text_only_no_ocr: "PDF images are not transcribed",
          docx_paragraphs_only: "Word tables and images are omitted", empty_text_rows_ignored: "Empty text rows skipped",
          no_text_extracted: "No text was extracted", soup_command_failed: "Soup could not process this source",
          earlier_source_failed: "Not processed because an earlier source failed",
          invalid_or_oversized_soup_output: "Soup output was invalid or too large"};
        for (const warning of source.warnings) card.append(element("p", warnings[warning] || warning));
      }
      for (const [label, id] of [["Download extracted dataset", item.output_artifact_id],
        ["Download ingestion record", item.manifest_artifact_id], ["Download Soup logs", item.logs_artifact_id]]) {
        if (!id) continue;
        const button = element("button", label);
        button.addEventListener("click", () => busy(button, () => download(`/artifacts/${id}/download`)));
        card.append(button);
      }
      list.append(card);
    }
  }
  document.querySelector("#document-ingest").addEventListener("click", event => busy(event.currentTarget, async () => {
    if (!selected.size) { message.textContent = "Select at least one document."; return; }
    message.textContent = "Processing documents with Soup…";
    const result = await api(`/datasets/${datasetId}/document-ingestions`, {source_ids: [...selected]});
    message.textContent = result.status === "succeeded" ? "Documents processed. Extracted data is ready to download."
      : "Processing failed. Originals are retained; inspect the warnings and logs below.";
    await refresh();
  }));
  document.addEventListener("dataset-refreshed", () => loadDocuments().catch(error => { message.textContent = error.message; }));
})();
