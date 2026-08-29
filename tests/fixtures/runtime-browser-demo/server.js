const express = require('express');
const app = express();

app.get('/', home);
app.get('/dashboard', dashboard);
app.get('/api/health', health);

function home(req, res) { res.sendFile('index.html'); }
function dashboard(req, res) { res.sendFile('dashboard.html'); }
function health(req, res) { res.json({ status: 'ok' }); }
