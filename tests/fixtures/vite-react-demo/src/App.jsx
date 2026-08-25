import { apiClient } from './api/client.js';

export default function App() {
  apiClient.get('/api/health');
  return <main>Vite React demo</main>;
}
