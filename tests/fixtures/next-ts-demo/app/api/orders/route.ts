import { createOrder } from "../../../src/services/orders";

export async function POST(request: Request) {
  const body = (await request.json()) as { userId: string; totalCents: number };
  const order = await createOrder(body.userId, body.totalCents);
  return Response.json(order, { status: 201 });
}
