(() => {
  const el = id => document.getElementById(id);
  let current = location.pathname.split('/')[2] || null;
  let messages = [], busy = false;
  const terminal = ['STOPPED', 'FAILED', 'LOST'];
  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1' + path, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Check the request fields and conversation length.');
    return value;
  }
  const error = e => { el('deployment-error').textContent = e.message; };
  function history() {
    el('chat-history').replaceChildren();
    for (const message of messages) {
      const row = document.createElement('p');
      row.style.whiteSpace = 'pre-wrap';
      row.textContent = `${message.role === 'user' ? 'You' : 'Model'}: ${message.content}`;
      el('chat-history').append(row);
    }
  }
  async function refresh() {
    try {
      const rows = await api('/deployments');
      el('deployment-list').replaceChildren();
      for (const row of rows) {
        const p = document.createElement('p'), link = document.createElement('a');
        link.href = '/deployments/' + row.id;
        link.textContent = `${row.id.slice(0, 8)} · ${row.status}`;
        p.append(link); el('deployment-list').append(p);
      }
      if (!current) return;
      const row = await api('/deployments/' + current);
      el('playground').hidden = false;
      const remaining = Math.max(0, Math.ceil((new Date(row.expires_at) - Date.now()) / 60000));
      el('deployment-status').textContent = `${row.status} · ${row.keep_running ? 'Running until stopped' : remaining + ' minutes remaining'} · ${row.precision.toUpperCase()}`;
      el('chat-send').disabled = busy || row.status !== 'RUNNING' || (!row.keep_running && remaining === 0);
      for (const name of ['extend', 'keep', 'stop']) el('deployment-' + name).disabled = terminal.includes(row.status) || row.status === 'STOP_REQUESTED';
    } catch (e) { error(e); }
  }
  el('deployment-create').addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    const button = event.submitter; button.disabled = true;
    try {
      const row = await api('/deployments', 'POST', {execution_target_id: el('deployment-target').value || null, model_version_id: el('deployment-model').value, artifact_id: el('deployment-artifact').value || null, precision: el('deployment-precision').value, lease_minutes: Number(el('deployment-lease').value)});
      current = row.id; messages = []; history();
      window.history.replaceState({}, '', '/deployments/' + current);
      el('deployment-error').textContent = ''; await refresh();
    } catch (e) { error(e); } finally { button.disabled = false; }
  });
  for (const action of ['extend', 'keep', 'stop']) el('deployment-' + action).addEventListener('click', async () => {
    try { await api(`/deployments/${current}/${action}`, 'POST', action === 'extend' ? {minutes: 30} : undefined); await refresh(); } catch (e) { error(e); }
  });
  el('chat-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !current) return;
    const content = el('chat-message').value.trim(); if (!content) return;
    busy = true; el('chat-send').disabled = true; el('chat-clear').disabled = true;
    const next = [...messages, {role: 'user', content}];
    try {
      const response = await api(`/deployments/${current}/chat/completions`, 'POST', {messages: next});
      messages = [...next, {role: 'assistant', content: response.content}];
      el('chat-message').value = ''; el('deployment-error').textContent = ''; history();
    } catch (e) { error(e); } finally { busy = false; el('chat-clear').disabled = false; await refresh(); }
  });
  el('chat-clear').addEventListener('click', () => { messages = []; history(); });
  (async () => {
    try {
      const models = await api('/models');
      for (const target of await api('/execution-targets')) {
        if (!['ssh_docker', 'kubernetes'].includes(target.kind)) continue;
        el('deployment-target').append(new Option(target.name, target.id));
      }
      for (const model of models) for (const version of model.versions) {
        if (!version.training_run_id && !version.sources.some(source => source.kind === 'upload')) continue;
        const option = document.createElement('option'); option.value = version.id; option.textContent = `${model.name} · version ${version.version}`; el('deployment-model').append(option);
      }
      const selected = new URLSearchParams(location.search).get('model_version_id');
      if (selected) el('deployment-model').value = selected;
      const representations = async () => {
        el('deployment-artifact').replaceChildren(new Option('Default', ''));
        for (const model of models) for (const version of model.versions) {
          if (version.id !== el('deployment-model').value) continue;
          for (const id of version.artifact_ids) {
            const artifact = await api('/artifacts/' + id);
            el('deployment-artifact').append(new Option(`${artifact.format} · ${artifact.size_bytes.toLocaleString()} bytes`, id));
          }
        }
      };
      el('deployment-model').addEventListener('change', () => representations().catch(error));
      await representations();
      await refresh();
    } catch (e) { error(e); }
  })();
  setInterval(refresh, 5000);
})();
