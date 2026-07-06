import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  message: string;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: "" };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message || "页面渲染出错" };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info);
  }

  private handleRetry = () => {
    this.setState({ hasError: false, message: "" });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="mx-auto max-w-lg p-8 text-center">
          <h1 className="text-xl font-semibold text-white">出错了</h1>
          <p className="mt-2 text-sm text-slate-400">{this.state.message}</p>
          <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
            <button type="button" className="btn-primary" onClick={this.handleRetry}>
              重试
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => window.location.reload()}
            >
              刷新页面
            </button>
            <button type="button" className="btn-secondary" onClick={() => window.location.assign("/")}>
              返回首页
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
