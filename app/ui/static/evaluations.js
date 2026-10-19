(() => {
  const $ = s => document.querySelector(s);
  const node = (tag, text) => { const value = document.createElement(tag); value.textContent = text; return value; };
  let id = location.pathname.split('/')[2];
  let runId = new URLSearchParams(location.search).get('training_run_id');
  let active = false;
  async function api(path, method = 'GET', body) {
    const response = await fetch(`/api/v1${path}`, {method, cache: 'no-store',
      headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw window.kitchenSoupRequestError(value, 'Check the evaluation inputs.');
    return value;
  }
  async function show(value) {
    id = value.id; runId = value.training_run_id;
    active = ['QUEUED', 'RUNNING'].includes(value.status);
    $('#evaluation-start').disabled = active;
    $('#evaluation-status').textContent = `Evaluation ${value.status}${value.status === 'FAILED' ? '. Completed responses are retained. Check GPU capacity and rebuild the trainer image, then explicitly resume missing responses.' : ''}`;
    $('#evaluation-summary').textContent = `Base better: ${value.summary.base_better}. Fine-tuned better: ${value.summary.fine_tuned_better}. Same: ${value.summary.same}. Unreviewed: ${value.summary.unreviewed}.`;
    const container = $('#evaluation-comparisons'); container.replaceChildren();
    for (const comparison of value.comparisons) {
      const article = node('article', ''); article.append(node('h2', comparison.prompt));
      const pair = node('div', ''); pair.style.display = 'grid'; pair.style.gridTemplateColumns = 'repeat(2, minmax(0, 1fr))'; pair.style.gap = '1rem';
      for (const [label, text] of [['Base model', comparison.base_response], ['Fine-tuned model', comparison.fine_tuned_response]]) {
        const panel = node('section', ''); const answer = node('pre', text ?? 'Waiting for response…'); answer.style.whiteSpace = 'pre-wrap';
        panel.append(node('h3', label), answer); pair.append(panel);
      }
      article.append(pair);
      for (const [preference, label] of [['base_better', 'Base better'], ['fine_tuned_better', 'Fine-tuned better'], ['same', 'Same']]) {
        const button = node('button', label); button.type = 'button'; button.disabled = value.status !== 'SUCCEEDED';
        button.setAttribute('aria-pressed', String(comparison.preference === preference));
        button.onclick = () => action(async () => show(await api(`/evaluations/${id}/preferences/${comparison.prompt_id}`, 'PUT', {preference})));
        article.append(button);
      }
      container.append(article);
    }
    $('#evaluation-report').hidden = !value.report_artifact_id;
    if (value.report_artifact_id) {
      $('#evaluation-report').href = `/artifacts?artifact_id=${value.report_artifact_id}`;
      $('#evaluation-report').onclick = event => {
        event.preventDefault();
        action(async () => {
          const grant = await api(`/artifacts/${value.report_artifact_id}/download`, 'POST');
          location.assign(grant.url);
        });
      };
    }
  }
  async function action(work) {
    try { await work(); } catch (error) { $('#evaluation-status').textContent = error.message; }
  }
  async function refresh() {
    if (id) return show(await api(`/evaluations/${id}`));
    if (runId) {
      const values = await api(`/evaluations?training_run_id=${runId}`);
      if (values.length) return show(values[0]);
      $('#evaluation-status').textContent = 'No evaluation generated yet. Training must have succeeded with a preselected suite.';
    }
  }
  $('#suite-form').onsubmit = event => {
    event.preventDefault(); const form = event.currentTarget, button = form.querySelector('button'); button.disabled = true;
    action(async () => {
      const suite = await api('/evaluation-suites', 'POST', {name: form.elements.name.value,
        prompts: form.elements.prompts.value.split('\n').map(s => s.trim()).filter(Boolean)});
      $('#evaluation-status').textContent = `Saved ${suite.name}. Choose this suite on the saved training plan before starting training.`;
      form.reset();
    }).finally(() => { button.disabled = false; });
  };
  $('#evaluation-start').onclick = () => {
    $('#evaluation-start').disabled = true;
    action(async () => { const task = await api('/evaluations', 'POST', {training_run_id: runId}); id = task.id; history.replaceState(null, '', `/evaluations/${id}`); await show(task); })
      .finally(() => { $('#evaluation-start').disabled = active; });
  };
  $('#evaluation-refresh').onclick = () => action(refresh);
  if (runId || id) {
    $('#suite-create').hidden = true; $('#evaluation-review').hidden = false;
    action(refresh); setInterval(() => { if (active && !document.hidden) action(refresh); }, 5000);
  } else action(async () => {
    for (const suite of await api('/evaluation-suites')) $('#suite-list').append(node('p', `${suite.name}: ${suite.prompts.length} prompts`));
  });
})();
