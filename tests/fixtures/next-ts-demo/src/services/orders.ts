import { db } from "../lib/db";

export type OrderRecord = {
  id: string;
  userId: string;
  totalCents: number;
};

export async function createOrder(userId: string, totalCents: number): Promise<OrderRecord> {
  return db.order.create({ data: { userId, totalCents } });
}
