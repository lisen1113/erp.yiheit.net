import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import Home from "./page";

it("renders the ERP entry page in Chinese", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("上海以和 ERP");
  expect(screen.getByText("业务管理系统工程基线已就绪。")).toBeTruthy();
});
