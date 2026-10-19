(() => {
  const el = id => document.getElementById(id);
  async function api(path, method = 'GET', body) {
    const response = await fetch('/api/v1/execution-targets' + path, {method, cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
    const value = await response.json();
    if (!response.ok) throw window.kitchenSoupRequestError(value, 'Check the execution target settings.');
    return value;
  }
  const error = e => { el('target-status').textContent = e.message; };
  async function load() {
    const targets = await api(''); el('target-list').replaceChildren();
    for (const target of targets) {
      const row = document.createElement('p'), button = document.createElement('button'), result = document.createElement('span');
      const location = target.kind === 'runpod_pod' ? `Runpod · ${target.configuration.gpu_id} · ${target.configuration.cloud}` : target.kind === 'sagemaker' ? `SageMaker · ${target.configuration.region} · ${target.configuration.instance_type}` : target.kind === 'kubernetes' ? `Kubernetes · ${target.configuration.namespace}` : `${target.configuration.username}@${target.configuration.host}:${target.configuration.port}`;
      row.append(document.createTextNode(`${target.name} · ${location} · ${target.id} `));
      button.textContent = target.kind === 'runpod_pod' ? 'Check API, GPU and price (no allocation)' : target.kind === 'sagemaker' ? 'Check S3 and ECR (no training job)' : target.kind === 'kubernetes' ? 'Test namespace and GPU capacity' : 'Test SSH, Docker and GPU'; button.type = 'button';
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
      if (data.deployment_image) configuration.deployment_image = data.deployment_image;
      await api('', 'POST', {name: data.name, kind: 'kubernetes', credential_ref: data.credential_ref, configuration});
      el('target-status').textContent = 'Kubernetes target created. Test it before selecting it for training.'; await load();
    } catch (e) { error(e); } finally { button.disabled = false; }
  });
  el('sagemaker-target-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(event.target));
      const configuration = {schema: 'kitchensoup.sagemaker-target/v1', region: data.region, role_arn: data.role_arn, training_image: data.training_image, staging_bucket: data.staging_bucket, staging_prefix: data.staging_prefix, instance_type: data.instance_type, max_runtime_seconds: Number(data.max_runtime_seconds), volume_gib: Number(data.volume_gib)};
      await api('', 'POST', {name: data.name, kind: 'sagemaker', credential_ref: data.credential_ref, configuration});
      el('target-status').textContent = 'SageMaker target created. Check S3/ECR access and GPU quota before submitting a paid run.'; await load();
    } catch (e) { error(e); } finally { button.disabled = false; }
  });
  el('runpod-target-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true;
    try {
      const data = Object.fromEntries(new FormData(event.target));
      const configuration = {schema: 'kitchensoup.runpod-target/v1', ssh_credential_ref: data.ssh_credential_ref, training_image: data.training_image, gpu_id: data.gpu_id, allowed_gpu_ids: [data.gpu_id], cloud: data.cloud, min_vram_gb: Number(data.min_vram_gb), max_hourly_price_usd: Number(data.max_hourly_price_usd), max_runtime_minutes: Number(data.max_runtime_minutes), disk_gb: Number(data.disk_gb), max_concurrent_pods: Number(data.max_concurrent_pods), data_center_ids: data.data_center_ids.split(',').map(s => s.trim()).filter(Boolean)};
      configuration.ssh_host_trust = data.ssh_host_trust;
      if (data.deployment_image) {
        configuration.deployment_image = data.deployment_image;
        configuration.deployment_min_vram_gb = Number(data.deployment_min_vram_gb);
        configuration.deployment_disk_gb = Number(data.deployment_disk_gb);
        configuration.max_deployment_lease_minutes = Number(data.max_deployment_lease_minutes);
        if (data.deployment_gpu_id) {
          configuration.deployment_gpu_id = data.deployment_gpu_id;
          configuration.allowed_gpu_ids = [...new Set([data.gpu_id, data.deployment_gpu_id])];
        }
      }
      await api('', 'POST', {name: data.name, kind: 'runpod_pod', credential_ref: data.credential_ref, configuration});
      el('target-status').textContent = 'Runpod target created. Check API access, price and host trust before submitting a paid run.'; await load();
    } catch (e) { error(e); } finally { button.disabled = false; }
  });
  load().catch(error);
})();
