import Link from "next/link";

export default function HomePage() {
  return (
    <main>
      <h1>Next TS Demo</h1>
      <nav>
        <Link href="/signup">Sign up</Link>
        <Link href="/checkout">Checkout</Link>
        <Link href="/admin">Admin</Link>
      </nav>
    </main>
  );
}
