import { createUser, listUsers } from "../../../src/services/users";

export async function GET() {
  const users = await listUsers();
  return Response.json(users);
}

export async function POST(request: Request) {
  const body = (await request.json()) as { email: string };
  const user = await createUser(body.email);
  return Response.json(user, { status: 201 });
}
