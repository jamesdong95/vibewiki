const express = require('express');
const app = express();

app.get('/api/health', health);

function health(req, res) {
  res.json({ status: 'ok' });
}
