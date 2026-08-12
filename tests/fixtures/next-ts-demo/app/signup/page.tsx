"use client";

import type { FormEvent } from "react";
import { useState } from "react";
import Link from "next/link";

type SignupResponse = { id: string; email: string };

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("Ready");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const response = await fetch('/api/users', {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email })
    });
    const user: SignupResponse = await response.json();
    setMessage(`Created ${user.email}`);
  }

  return (
    <main>
      <h1>Sign up</h1>
      <form onSubmit={handleSubmit}>
        <label htmlFor="email">Email</label>
        <input id="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        <button type="submit">Create account</button>
      </form>
      <p>{message}</p>
      <Link href="/">Back</Link>
    </main>
  );
}
