(() => {
  const parts = window.location.pathname.split('/'), dataset = parts[2], version = parts[4];
  const base = `/api/v1/datasets/${dataset}/versions/${version}`;
  let offset = 0, total = 0, loading = false;
  const status = document.querySelector('#preview-status');
  function node(tag, text) { const n = document.createElement(tag); n.textContent = text; if (tag === 'pre') { n.style.whiteSpace = 'pre-wrap'; n.style.overflowWrap = 'anywhere'; } return n; }
  async function request(url, method = 'GET') {
    const response = await fetch(url, {method}); const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not load this dataset version.');
    return body;
  }
  async function preview() {
    if (loading) return;
    loading = true;
    const previous = document.querySelector('#preview-previous'), next = document.querySelector('#preview-next');
    previous.disabled = next.disabled = true;
    try {
      const limit = Number(document.querySelector('#preview-limit').value);
      const page = await request(`${base}/examples?offset=${offset}&limit=${limit}`); total = page.total;
      const list = document.querySelector('#preview-examples'); list.replaceChildren();
      page.items.forEach((example, index) => {
        const card = node('article', ''); card.append(node('h3', `Example ${offset + index + 1} · ${example.kind}`));
        if (example.kind === 'conversation') {
          example.messages.forEach((message, position) => {
            card.append(node('h4', position === example.messages.length - 1 ? 'Assistant target' : message.role), node('pre', message.content));
          });
        } else card.append(node('pre', example.text));
        card.append(node('p', `Source: ${example.source_id}`)); list.append(card);
      });
      document.querySelector('#preview-position').textContent = `${total ? offset + 1 : 0}–${offset + page.items.length} of ${total}`;
      previous.disabled = offset === 0; next.disabled = offset + limit >= total;
      status.textContent = '';
    } catch (error) { status.textContent = error.message; }
    finally { loading = false; }
  }
  document.querySelector('#preview-previous').addEventListener('click', () => { if (!loading) { offset = Math.max(0, offset - Number(document.querySelector('#preview-limit').value)); preview(); } });
  document.querySelector('#preview-next').addEventListener('click', () => { if (!loading) { offset += Number(document.querySelector('#preview-limit').value); preview(); } });
  document.querySelector('#preview-limit').addEventListener('change', () => { if (!loading) { offset = 0; preview(); } });
  async function load() {
    const item = await request(base), manifest = item.manifest, stats = manifest.statistics;
    document.querySelector('#version-back').href = `/datasets/${dataset}`;
    document.querySelector('#version-title').textContent = `Dataset version ${item.version}`;
    document.querySelector('#version-statistics').textContent = `${stats.source_count} sources · ${stats.conversation_count} conversations · ${stats.message_count} messages · ${stats.example_count} examples · ${stats.ignored_item_count} known ignored items · ${stats.size_bytes.toLocaleString()} bytes`;
    document.querySelector('#version-license').textContent = `Dataset license: ${manifest.license || 'Not specified'}`;
    for (const source of manifest.sources) document.querySelector('#version-sources').append(node('p', `${source.filename} · ${source.source_id} · SHA-256 ${source.artifact.sha256} · License: ${source.license || 'Not specified'}`));
    for (const warning of manifest.warnings) document.querySelector('#version-warnings').append(node('p', `${warning.code.replaceAll('_', ' ')} · Source ${warning.source_id}${warning.count === null ? '' : ` · ${warning.count}`}`));
    if (!manifest.warnings.length) document.querySelector('#version-warnings').append(node('p', 'No validation warnings.'));
    for (const [title, id] of [['Download exact examples', manifest.examples.artifact_id], ['Download manifest', item.manifest_artifact_id]]) {
      const button = node('button', title); button.addEventListener('click', async () => {
        try { const grant = await request(`/api/v1/artifacts/${id}/download`, 'POST'); window.location.assign(grant.url); }
        catch (error) { status.textContent = error.message; }
      }); document.querySelector('#version-downloads').append(button);
    }
    await preview();
  }
  load().catch(error => { status.textContent = error.message; });
})();
