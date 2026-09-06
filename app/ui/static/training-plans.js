(() => {
  const $ = selector => document.querySelector(selector);
  const form = $('#plan-form'), status = $('#plan-status');
  let recipes = [], versions = [], preview = null;
  const fields = {
    epochs: ['Epochs', 1, 20, 1], learning_rate: ['Learning rate', 0.000000001, 0.01, 'any'],
    max_sequence_length: ['Maximum sequence length', 128, 32768, 1], batch_size: ['Batch size per device', 1, 64, 1],
    gradient_accumulation_steps: ['Gradient accumulation steps', 1, 256, 1], lora_rank: ['LoRA rank', 1, 256, 1],
    lora_alpha: ['LoRA alpha', 1, 512, 1], lora_dropout: ['LoRA dropout', 0, 0.999999, 'any'], seed: ['Random seed', 0, 2147483647, 1]
  };
  function node(tag, text) { const item = document.createElement(tag); item.textContent = text; return item; }
  async function request(path, method = 'GET', body, hash) {
    const headers = {'Content-Type': 'application/json'};
    if (hash) headers['X-Plan-SHA256'] = hash;
    const response = await fetch(`/api/v1${path}`, {method, headers, body: body === undefined ? undefined : JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Check the selected inputs and advanced parameter limits.');
    return value;
  }
  function invalidate() { preview = null; $('#plan-review').hidden = true; }
  function recipe() { return recipes.find(item => `${item.id}:${item.version}` === $('#plan-recipe').value); }
  function defaults() {
    const chosen = recipe();
    $('#plan-defaults').textContent = chosen ? JSON.stringify(chosen.defaults, null, 2) : '';
    for (const name of Object.keys(fields)) form.elements[name].placeholder = chosen ? String(chosen.defaults[name]) : '';
  }
  function intent() {
    const selected = $('#plan-intent').value, advanced = selected === 'advanced';
    $('#intent-help').textContent = selected === 'learn_from_documents'
      ? 'Fine-tuning is unreliable for adding or updating factual knowledge. Consider retrieval over your documents instead. This recipe adapts to document text without generating question-answer pairs. Choose a documents-only version.'
      : advanced ? 'Choose a versioned recipe and inspect or override its parameters below.'
      : 'Choose a conversations-only version with user inputs and desired assistant answers. Task examples use the same input/answer format.';
    $('#plan-recipe').replaceChildren();
    for (const item of recipes.filter(item => advanced || item.intent === selected)) {
      const option = node('option', `${item.name} (v${item.version})`); option.value = `${item.id}:${item.version}`; $('#plan-recipe').append(option);
    }
    if (advanced) $('#plan-advanced').open = true;
    defaults();
  }
  function datasetLink() {
    const selected = versions.find(item => item.id === $('#plan-dataset').value);
    $('#plan-dataset-preview').href = selected ? `/datasets/${selected.dataset_id}/versions/${selected.id}` : '/datasets';
  }
  function render(value) {
    $('#plan-summary').replaceChildren();
    const resolved = value.resolved, summary = $('#plan-summary');
    summary.append(node('p', `Intent: ${value.appspec.intent.replaceAll('_', ' ')}`),
      node('p', `Model: ${resolved.base_model_name}. Dataset: ${resolved.dataset_name}, version ${resolved.dataset_version}, ${resolved.example_count} examples.`),
      node('p', `Recipe: ${resolved.recipe.name} (v${resolved.recipe.version}). Output: LoRA adapter.`));
    const link = node('a', 'Review the exact dataset examples'); link.href = `/datasets/${resolved.dataset_id}/versions/${resolved.dataset_version_id}`; summary.append(link);
    for (const warning of resolved.warnings) summary.append(node('p', warning));
    if (value.id) summary.append(node('p', `Saved ${new Date(value.created_at).toLocaleString()}. No training run was submitted.`));
    $('#plan-json').textContent = JSON.stringify(value, null, 2);
    $('#plan-review').hidden = false;
    $('#plan-save').hidden = Boolean(value.id);
  }
  for (const [name, [title, min, max, step]] of Object.entries(fields)) {
    const label = node('label', title + ' '), input = document.createElement('input');
    input.type = 'number'; input.name = name; input.min = min; input.max = max; input.step = step;
    label.append(input); $('#plan-overrides').append(label);
  }
  form.addEventListener('input', invalidate);
  form.addEventListener('change', invalidate);
  $('#plan-intent').addEventListener('change', intent);
  $('#plan-recipe').addEventListener('change', defaults);
  $('#plan-dataset').addEventListener('change', datasetLink);
  form.addEventListener('submit', async event => {
    event.preventDefault(); invalidate();
    const chosen = recipe(), overrides = {};
    for (const name of Object.keys(fields)) if (form.elements[name].value !== '') overrides[name] = Number(form.elements[name].value);
    if (!chosen) { status.textContent = 'Choose a recipe.'; return; }
    const spec = {schema: 'kitchensoup.appspec/v1', intent: $('#plan-intent').value,
      base_model_version_id: $('#plan-model').value, dataset_version_id: $('#plan-dataset').value,
      recipe: {id: chosen.id, version: chosen.version}, overrides};
    const button = form.querySelector('[type=submit]'); button.disabled = true;
    // Freeze fields while resolving so a late response cannot review different inputs.
    for (const control of form.elements) control.disabled = true;
    try { preview = await request('/training-plans/preview', 'POST', spec); render(preview); status.textContent = 'Review the plan below, then save it.'; }
    catch (error) { status.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  });
  $('#plan-save').addEventListener('click', async () => {
    if (!preview) return;
    const reviewed = preview; $('#plan-save').disabled = true;
    try { const saved = await request('/training-plans', 'POST', reviewed.appspec, reviewed.sha256); location.assign(`/training-plans/${saved.id}`); }
    catch (error) { status.textContent = error.message; }
    finally { $('#plan-save').disabled = false; }
  });
  async function load() {
    const id = location.pathname.split('/')[2];
    if (id) {
      form.hidden = true; $('#plan-history').hidden = true;
      render(await request(`/training-plans/${id}`)); return;
    }
    const [loadedRecipes, models, datasets, plans] = await Promise.all([
      request('/recipes'), request('/models'), request('/datasets'), request('/training-plans')
    ]);
    recipes = loadedRecipes; intent();
    for (const model of models) for (const version of model.versions) {
      const option = node('option', `${model.name} (v${version.version})`); option.value = version.id; $('#plan-model').append(option);
    }
    const batches = await Promise.all(datasets.map(dataset => request(`/datasets/${dataset.id}/versions`)));
    versions = batches.flat();
    for (const version of versions) {
      const dataset = datasets.find(item => item.id === version.dataset_id);
      const option = node('option', `${dataset.name} (v${version.version}, ${version.manifest.statistics.example_count} examples)`); option.value = version.id; $('#plan-dataset').append(option);
    }
    const selected = new URLSearchParams(location.search).get('dataset_version_id');
    if (versions.some(item => item.id === selected)) $('#plan-dataset').value = selected;
    datasetLink();
    for (const plan of plans) {
      const link = node('a', `${plan.resolved.recipe.name} · ${plan.resolved.dataset_name} v${plan.resolved.dataset_version} · ${new Date(plan.created_at).toLocaleString()}`);
      link.href = `/training-plans/${plan.id}`; const p = node('p', ''); p.append(link); $('#plan-list').append(p);
    }
    if (!plans.length) $('#plan-list').textContent = 'No saved plans yet.';
    if (!models.length || !versions.length) status.textContent = 'Register a base model and create a dataset version before reviewing a plan.';
  }
  load().catch(error => { status.textContent = error.message; });
})();
