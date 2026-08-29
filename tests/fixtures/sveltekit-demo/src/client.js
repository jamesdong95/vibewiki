export async function loadHealth() {
  return fetch('/api/health');
}
