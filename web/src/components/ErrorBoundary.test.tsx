// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(() => cleanup());

function Boom(): never {
  throw new Error("chunk failed");
}

describe("ErrorBoundary", () => {
  it("renders retry and reload actions when a child throws", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );

    expect(screen.getByText("出错了")).toBeTruthy();
    expect(screen.getByText("chunk failed")).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "刷新页面" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "返回首页" })).toBeTruthy();

    consoleError.mockRestore();
  });

  it("retry clears error state and re-renders children", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    let shouldThrow = true;
    function MaybeBoom() {
      if (shouldThrow) throw new Error("first fail");
      return <div>recovered</div>;
    }

    render(
      <ErrorBoundary>
        <MaybeBoom />
      </ErrorBoundary>,
    );

    expect(screen.getByText("first fail")).toBeTruthy();
    shouldThrow = false;
    fireEvent.click(screen.getByText("重试"));
    expect(screen.getByText("recovered")).toBeTruthy();

    consoleError.mockRestore();
  });

  it("getDerivedStateFromError uses fallback message", () => {
    expect(ErrorBoundary.getDerivedStateFromError(new Error())).toEqual({
      hasError: true,
      message: "页面渲染出错",
    });
  });
});

describe("ErrorBoundary lazy chunk failure", () => {
  it("catches render errors from lazy-like failures", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});

    class LazyFail extends Component {
      render(): ReactNode {
        throw new Error("dynamic import failed");
      }
    }

    render(
      <ErrorBoundary>
        <LazyFail />
      </ErrorBoundary>,
    );

    expect(screen.getByText("dynamic import failed")).toBeTruthy();
    consoleError.mockRestore();
  });
});
