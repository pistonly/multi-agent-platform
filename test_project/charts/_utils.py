from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def output_dir() -> Path:
    path = project_root() / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_chinese_font() -> None:
    for font in ("Noto Sans CJK SC", "SimHei", "WenQuanYi Micro Hei", "DejaVu Sans"):
        try:
            plt.rcParams["font.sans-serif"] = [font]
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


def save_figure(fig: plt.Figure, chart_type: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir() / f"{chart_type}_{timestamp}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path
