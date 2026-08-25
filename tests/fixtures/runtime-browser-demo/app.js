fetch('/api/health')
  .then(response => response.json())
  .then(payload => document.body.dataset.health = payload.status);

console.error('runtime fixture console error');
