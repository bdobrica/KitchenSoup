(() => {
  const $ = selector => document.querySelector(selector);
  const id = location.pathname.split('/')[2];
  const cursor = {stdout: 0, stderr: 0};
  let busy = false;
  let events = null;
  const terminal = ['SUCCEEDED', 'FAILED', 'CANCELED', 'LOST'];
  async function request(path, method = 'GET') {
    const response = await fetch(`/api/v1/training-runs${path}`, {method, cache: 'no-store'});
    if (response.status === 204) return;
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : 'Training request failed');
    return value;
  }
  async function refresh() {
    if (busy) return;
    busy = true;
    try {
      if (!id) {
        const runs = await request(''); $('#run-list').replaceChildren();
        for (const run of runs) {
          const p = document.createElement('p'), link = document.createElement('a');
          link.textContent = `${new Date(run.created_at).toLocaleString()} · ${run.status} · ${run.id}`;
          link.href = `/training-runs/${run.id}`; p.append(link); $('#run-list').append(p);
        }
        if (!runs.length) $('#run-list').textContent = 'No training runs yet. Submit a saved plan to begin.';
        return;
      }
      $('#run-detail').hidden = false;
      const run = await request(`/${id}`);
      $('#run-status').textContent = `Status: ${run.status}${run.observation?.message ? '. ' + run.observation.message : ''}`;
      $('#run-json').textContent = JSON.stringify(run, null, 2);
      $('#run-plan').href = `/training-plans/${run.plan_id}`;
      const active = !terminal.includes(run.status);
      $('#run-retry').disabled = active;
      $('#run-cancel').disabled = !['QUEUED', 'PREPARING', 'SUBMITTED', 'RUNNING'].includes(run.status);
      $('#run-cleanup').disabled = active;
      for (const stream of ['stdout', 'stderr']) {
        const chunk = await request(`/${id}/logs?stream=${stream}&cursor=${cursor[stream]}`);
        cursor[stream] = chunk.cursor;
        const log = $(`#run-${stream}`);
        log.textContent = (log.textContent + chunk.text).slice(-262144);
      }
      $('#run-message').textContent = '';
    } catch (error) { $('#run-message').textContent = error.message; }
    finally { busy = false; }
  }
  for (const [button, action, method] of [['cancel', 'cancel', 'POST'], ['cleanup', 'container', 'DELETE']]) {
    $(`#run-${button}`).onclick = async () => {
      $(`#run-${button}`).disabled = true;
      try { await request(`/${id}/${action}`, method); await refresh(); }
      catch (error) { $('#run-message').textContent = error.message; }
      finally { $(`#run-${button}`).disabled = false; }
    };
  }
  $('#run-retry').onclick = async () => {
    $('#run-retry').disabled = true;
    try {
      const run = await request(`/${id}/clone`, 'POST');
      location.assign(`/training-runs/${run.id}`);
    } catch (error) { $('#run-message').textContent = error.message; $('#run-retry').disabled = false; }
  };
  if (id) {
    events = new EventSource(`/api/v1/training-runs/${id}/events`);
    events.addEventListener('transition', event => {
      const change = JSON.parse(event.data);
      if (document.getElementById(`event-${change.sequence}`)) return;
      const item = document.createElement('li');
      item.id = `event-${change.sequence}`;
      item.textContent = `${new Date(change.created_at).toLocaleString()} · ${change.status}${change.message ? ' · ' + change.message : ''}`;
      $('#run-events').append(item);
    });
    events.addEventListener('status', event => {
      const run = JSON.parse(event.data);
      if (terminal.includes(run.status) && events) { events.close(); events = null; }
      refresh();
    });
    window.addEventListener('pagehide', () => { if (events) events.close(); });
  }
  $('#run-refresh').onclick = refresh;
  refresh();
  if (id) setInterval(() => { if (!document.hidden) refresh(); }, 5000);
})();
