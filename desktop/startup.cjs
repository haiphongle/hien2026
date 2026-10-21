window.frameweaver.onStatus(({ message, retry, reuse }) => {
  document.querySelector('#status').textContent = message;
  document.querySelector('[data-action="retry"]').hidden = !retry;
  document.querySelector('[data-action="reuse"]').hidden = !reuse;
});
document.querySelectorAll('[data-action]').forEach((button) => {
  button.addEventListener('click', () => window.frameweaver.action(button.dataset.action));
});
window.frameweaver.action('ready');
