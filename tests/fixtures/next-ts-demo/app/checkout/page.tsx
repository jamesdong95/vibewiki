"use client";

import { useState } from "react";
import Link from "next/link";

type OrderResponse = { id: string; totalCents: number };

export default function CheckoutPage() {
  const [message, setMessage] = useState("Ready");

  async function handleCheckout() {
    const response = await fetch('/api/orders', {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ userId: "demo-user", totalCents: 1200 })
    });
    const order: OrderResponse = await response.json();
    setMessage(`Order ${order.id} created for ${order.totalCents} cents`);
  }

  return (
    <main>
      <h1>Checkout</h1>
      <button type="button" onClick={handleCheckout}>Place demo order</button>
      <p>{message}</p>
      <Link href="/">Back</Link>
    </main>
  );
}
