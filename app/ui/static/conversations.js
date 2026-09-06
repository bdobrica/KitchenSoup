(() => {
  const id = window.location.pathname.split("/")[2];
  if (!id) return;
  const status = document.querySelector("#conversation-status");
  let rows = [], selected = new Set(), dirty = false;
  const warnings = {
    attachments_ignored: "Attachments ignored", multimodal_ignored: "Image/audio or other multimodal content ignored",
    tool_content_ignored: "Tool calls/output ignored", unknown_role_ignored: "Unknown message roles ignored",
    alternate_branches_ignored: "Alternate branches ignored; current branch imported",
    hidden_messages_ignored: "Hidden messages ignored", unsupported_content_ignored: "Unsupported content ignored",
    developer_role_normalized: "Developer role normalized to system",
  };
  function visible() {
    const query = document.querySelector("#conversation-search").value.toLocaleLowerCase();
    return rows.filter(row => row.title.toLocaleLowerCase().includes(query));
  }
  function render() {
    const list = document.querySelector("#conversation-list"); list.replaceChildren();
    for (const row of visible()) {
      const card = element("article", ""), label = element("label", ""), checkbox = document.createElement("input");
      checkbox.type = "checkbox"; checkbox.checked = selected.has(row.id);
      checkbox.addEventListener("change", () => {
        checkbox.checked ? selected.add(row.id) : selected.delete(row.id); dirty = true; render();
      });
      label.append(checkbox, document.createTextNode(` ${row.title}`));
      card.append(label, element("p", `${row.source_created_at ? new Date(row.source_created_at).toLocaleDateString() : "Date unavailable"} · ${row.message_count} text messages`));
      for (const warning of row.warnings) card.append(element("p", warnings[warning] || warning));
      list.append(card);
    }
    if (!visible().length) list.append(element("p", rows.length ? "No matching conversations." : "Import a ChatGPT ZIP to see conversations here."));
    status.textContent = `${selected.size} of ${rows.length} selected${dirty ? " — unsaved changes" : ""}.`;
  }
  async function load() {
    try {
      rows = await api(`/datasets/${id}/conversations`);
      if (!dirty) selected = new Set(rows.filter(row => row.selected).map(row => row.id));
      render();
    } catch (error) { status.textContent = error.message; }
  }
  document.querySelector("#conversation-search").addEventListener("input", render);
  document.querySelector("#conversation-all").addEventListener("click", () => {
    for (const row of visible()) selected.add(row.id); dirty = true; render();
  });
  document.querySelector("#conversation-none").addEventListener("click", () => { selected.clear(); dirty = true; render(); });
  document.querySelector("#conversation-save").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    const button = event.currentTarget;
    try {
      rows = await api(`/datasets/${id}/conversation-selection`, {conversation_ids: [...selected]}, "PUT");
      dirty = false; selected = new Set(rows.filter(row => row.selected).map(row => row.id)); render();
      status.textContent += " Selection saved.";
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
  document.addEventListener("dataset-refreshed", load);
  load();
})();
