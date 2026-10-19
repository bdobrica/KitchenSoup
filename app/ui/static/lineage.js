// Render typed graph data locally. Labels are always text, never HTML or code.
window.showLineage = async function (container, path, title) {
  const response = await fetch(path);
  const graph = await response.json();
  if (!response.ok) throw window.kitchenSoupRequestError(graph, 'Lineage unavailable');
  const section = document.createElement('section'), heading = document.createElement('h4');
  heading.textContent = title; section.append(heading);
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  const positions = new Map(graph.nodes.map((node, i) => [node.id, 50 + i * 85]));
  svg.setAttribute('viewBox', `0 0 650 ${Math.max(100, graph.nodes.length * 85)}`);
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', title + ': arrows point from parent to child');
  svg.style.width = '100%'; svg.style.maxWidth = '650px';
  const defs = document.createElementNS(ns, 'defs'), marker = document.createElementNS(ns, 'marker');
  const markerId = `arrow-${graph.root_id}`;
  marker.id = markerId; marker.setAttribute('markerWidth', '10'); marker.setAttribute('markerHeight', '10');
  marker.setAttribute('refX', '8'); marker.setAttribute('refY', '3'); marker.setAttribute('orient', 'auto');
  const tip = document.createElementNS(ns, 'path'); tip.setAttribute('d', 'M0,0 L0,6 L9,3 z'); tip.setAttribute('fill', 'currentColor');
  marker.append(tip); defs.append(marker); svg.append(defs);
  for (const edge of graph.edges) {
    const line = document.createElementNS(ns, 'path');
    const start = positions.get(edge.parent_id), end = positions.get(edge.child_id);
    line.setAttribute('d', `M40,${start} Q0,${(start + end) / 2} 40,${end}`);
    line.setAttribute('fill', 'none'); line.setAttribute('stroke', 'currentColor');
    line.setAttribute('marker-end', `url(#${markerId})`); svg.append(line);
  }
  for (const node of graph.nodes) {
    const text = document.createElementNS(ns, 'text'); text.setAttribute('x', '55');
    text.setAttribute('y', String(positions.get(node.id))); text.setAttribute('fill', 'currentColor');
    text.textContent = node.label.length > 65 ? node.label.slice(0, 62) + '…' : node.label;
    const label = document.createElementNS(ns, 'title'); label.textContent = `${node.label} (${node.id})`; text.append(label); svg.append(text);
  }
  section.append(svg);
  const list = document.createElement('ul');
  for (const node of graph.nodes) {
    const item = document.createElement('li'); item.textContent = `${node.label} · ${node.id}`;
    if (node.model_id) {
      const link = document.createElement('a'); link.href = `/models/${node.model_id}`; link.textContent = ' Open model'; item.append(link);
    }
    if (node.sha256) item.append(document.createTextNode(` · ${node.size_bytes} bytes · SHA-256: ${node.sha256}`));
    for (const source of node.sources) {
      const p = document.createElement('p'); p.textContent = `License: ${source.license} · ${source.location} · ${source.revision || 'uploaded artifact'}`; item.append(p);
    }
    list.append(item);
  }
  for (const edge of graph.edges) {
    const item = document.createElement('li'); item.textContent = `${edge.parent_id} → ${edge.child_id} (${edge.operation})`; list.append(item);
  }
  section.append(list);
  const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
  summary.textContent = 'Mermaid graph data'; pre.textContent = graph.mermaid; details.append(summary, pre); section.append(details);
  container.append(section);
};
