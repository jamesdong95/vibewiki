import { describe, expect, it } from "vitest";
import SignupPage from "../app/signup/page";
import { createUser } from "../src/services/users";

describe("signup flow", () => {
  it("exposes the signup page and user creation subject", () => {
    expect(SignupPage).toBeDefined();
    expect(createUser).toBeDefined();
  });
});
