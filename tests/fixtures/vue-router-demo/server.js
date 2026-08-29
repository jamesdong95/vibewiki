const app = createApp();

app.get('/api/health', health);

function health(req, res) {
  res.send('ok');
}

export default app;
