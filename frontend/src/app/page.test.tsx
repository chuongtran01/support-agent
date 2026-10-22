import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import Home from "./page";

test("the home page says what the app is", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: /northwind outfitters support/i })).toBeInTheDocument();
});
