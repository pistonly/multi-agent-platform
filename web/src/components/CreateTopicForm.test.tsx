// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CreateTopicForm } from "./CreateTopicForm";

afterEach(() => cleanup());

describe("CreateTopicForm", () => {
  it("does not submit a create-topic API; it shows a copyable fs command", () => {
    render(<CreateTopicForm />);

    expect(screen.getByText(/平台不再接收「发布话题」API/)).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText(/一条讨论/), {
      target: { value: "Ship the CLI guide" },
    });

    expect(
      screen.getByText(
        'map --persona host fs topic-create --title "Ship the CLI guide" --slug ship-the-cli-guide',
      ),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "发布话题" })).toBeNull();
  });
});
