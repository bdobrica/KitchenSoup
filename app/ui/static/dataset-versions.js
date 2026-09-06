(() => {
  if (!datasetId) return;
  const chosen = new Set();
  const message = document.querySelector('#version-status');
  async function load() {
    const extractions = await api(`/datasets/${datasetId}/document-ingestions`);
    const options = document.querySelector('#version-documents'); options.replaceChildren();
    for (const item of extractions.filter(item => item.status === 'succeeded')) {
      const label = element('label', '');
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = chosen.has(item.id);
      checkbox.addEventListener('change', () => checkbox.checked ? chosen.add(item.id) : chosen.delete(item.id));
      label.append(checkbox, document.createTextNode(`Document extraction ${item.version}: ${item.manifest.example_count} rows (${item.manifest.sources.map(s => s.filename).join(', ')})`));
      options.append(label);
    }
    const versions = await api(`/datasets/${datasetId}/versions`);
    const list = document.querySelector('#version-list'); list.replaceChildren();
    for (const item of versions) {
      const link = element('a', `Version ${item.version}: ${item.manifest.statistics.example_count} examples`);
      link.href = `/datasets/${datasetId}/versions/${item.id}`;
      const row = element('p', ''); row.append(link); list.append(row);
    }
  }
  document.querySelector('#version-save').addEventListener('click', event => busy(event.currentTarget, async () => {
    message.textContent = 'Creating immutable dataset version…';
    const body = {document_ingestion_ids: [...chosen]};
    if (!document.querySelector('#version-conversations').checked) body.conversation_ids = [];
    const version = await api(`/datasets/${datasetId}/versions`, body);
    window.location.assign(`/datasets/${datasetId}/versions/${version.id}`);
  }));
  document.addEventListener('dataset-refreshed', () => load().catch(error => { message.textContent = error.message; }));
})();
