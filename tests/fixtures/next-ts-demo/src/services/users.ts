import { db } from "../lib/db";

export type UserRecord = {
  id: string;
  email: string;
};

export async function createUser(email: string): Promise<UserRecord> {
  return db.user.create({ data: { email } });
}

export async function listUsers(): Promise<UserRecord[]> {
  return db.user.findMany({ orderBy: { createdAt: "asc" } });
}
