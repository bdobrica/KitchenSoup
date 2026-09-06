(() => {
  const form = document.querySelector('#provider-form'), status = document.querySelector('#provider-status');
  let editing = null;
  function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
  async function request(path, method = 'GET', body) {
    const response = await fetch(`/api/v1/llm-providers${path}`, {method, headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Check provider settings.');
    return result;
  }
  function reset() { editing = null; form.reset(); document.querySelector('#provider-form-title').textContent = 'Add provider'; }
  async function load() {
    const providers = await request('');
    const list = document.querySelector('#provider-list'); list.replaceChildren();
    for (const provider of providers) {
      const card = node('article', '');
      card.append(node('h3', provider.name), node('p', `Destination: ${provider.base_url}`), node('p', `Credential: ${provider.api_key_ref || 'No authentication'}`));
      const edit = node('button', 'Edit provider');
      edit.addEventListener('click', () => {
        editing = provider.id; document.querySelector('#provider-form-title').textContent = `Edit ${provider.name}`;
        for (const name of ['name', 'base_url', 'api_key_ref']) form.elements[name].value = provider[name] || '';
        form.elements.model_names.value = provider.model_names.join('\n'); form.scrollIntoView();
      });
      const select = document.createElement('select'); select.setAttribute('aria-label', `Test model for ${provider.name}`);
      for (const model of provider.model_names) { const option = node('option', model); option.value = model; select.append(option); }
      card.append(edit, select);
      for (const mode of ['chat', 'structured']) {
        const button = node('button', mode === 'chat' ? 'Test connection' : 'Test structured output');
        button.addEventListener('click', async () => {
          button.disabled = true; status.textContent = `Sending a synthetic ${mode} test to ${provider.base_url}…`;
          try { const result = await request(`/${provider.id}/test`, 'POST', {model: select.value, mode}); status.textContent = result.message; }
          catch (error) { status.textContent = error.message; }
          finally { button.disabled = false; }
        }); card.append(button);
      }
      list.append(card);
    }
    if (!providers.length) list.append(node('p', 'No providers configured.'));
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); const button = form.querySelector('button[type=submit]'); button.disabled = true;
    const fields = Object.fromEntries(new FormData(form));
    fields.model_names = fields.model_names.split(/\r?\n/).map(name => name.trim()).filter(Boolean);
    fields.api_key_ref = fields.api_key_ref.trim() || null;
    try { await request(editing ? `/${editing}` : '', editing ? 'PUT' : 'POST', fields); reset(); await load(); status.textContent = 'Provider settings saved. No source content was sent.'; }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.querySelector('#provider-new').addEventListener('click', reset);
  load().catch(error => { status.textContent = error.message; });
})();
