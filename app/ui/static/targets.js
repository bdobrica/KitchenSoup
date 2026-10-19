(() => {
  const el = id => document.getElementById(id);
  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1/execution-targets' + path, {method, cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Check the SSH target settings.');
    return value;
  }
  const error = e => { el('target-status').textContent = e.message; };
  async function load() {
    const targets = await api(''); el('target-list').replaceChildren();
    for (const target of targets) {
      const row = document.createElement('p'), button = document.createElement('button'), result = document.createElement('span');
      const location = target.kind === 'kubernetes' ? `Kubernetes · ${target.configuration.namespace}` : `${target.configuration.username}@${target.configuration.host}:${target.configuration.port}`;
      row.append(document.createTextNode(`${target.name} · ${location} · ${target.id} `));
      button.textContent = target.kind === 'kubernetes' ? 'Test namespace and GPU capacity' : 'Test SSH, Docker and GPU'; button.type = 'button';
      button.onclick = async () => {
        button.disabled = true; result.textContent = ' Checking…';
        try { const value = await api('/' + target.id + '/test', 'POST'); result.textContent = ` ${value.message}. ${value.gpu}`; }
        catch (e) { result.textContent = ' ' + e.message; } finally { button.disabled = false; }
      };
      row.append(button, result); el('target-list').append(row);
    }
  }
  el('target-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    const data = Object.fromEntries(new FormData(event.target));
    const body = {name: data.name, kind: 'ssh_docker', credential_ref: data.credential_ref, configuration: {schema: 'kitchensoup.ssh-target/v1', host: data.host, port: Number(data.port), username: data.username, workspace: data.workspace, training_image: data.training_image, deployment_image: data.deployment_image}};
    try { await api('', 'POST', body); el('target-status').textContent = 'Target created. Test the connection before selecting it for training or serving.'; await load(); }
    catch (e) { error(e); } finally { button.disabled = false; }
  });
  el('kubernetes-target-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(event.target));
      const configuration = {schema: 'kitchensoup.kubernetes-target/v1', namespace: data.namespace, training_image: data.training_image, context: data.context || null, runtime_class: data.runtime_class || null, service_account: data.service_account, gpu_count: Number(data.gpu_count), storage_class: data.storage_class || null, workspace_gib: Number(data.workspace_gib), node_selector: JSON.parse(data.node_selector), tolerations: JSON.parse(data.tolerations)};
      await api('', 'POST', {name: data.name, kind: 'kubernetes', credential_ref: data.credential_ref, configuration});
      el('target-status').textContent = 'Kubernetes target created. Test it before selecting it for training.'; await load();
    } catch (e) { error(e); } finally { button.disabled = false; }
  });
  load().catch(error);
})();
