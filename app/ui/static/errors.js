/* Error details are normalized by the server; never render them as HTML. */
window.kitchenSoupRequestError = (value, fallback) => {
  const details = document.getElementById('advanced-error');
  const text = document.getElementById('advanced-error-text');
  if (details && text) {
    text.textContent = value.advanced_detail || '';
    details.hidden = !value.advanced_detail;
  }
  return new Error(typeof value.detail === 'string' ? value.detail : fallback);
};
