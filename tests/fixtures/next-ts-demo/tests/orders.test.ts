import { describe, expect, it } from "vitest";
import CheckoutPage from "../app/checkout/page";
import { createOrder } from "../src/services/orders";

describe("orders flow", () => {
  it("exposes checkout and order creation subjects", () => {
    expect(CheckoutPage).toBeDefined();
    expect(createOrder).toBeDefined();
  });
});
