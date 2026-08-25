export default function users(req, res) {
  if (req.method === 'GET') res.json({ users: [] });
}
