from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from charts._utils import configure_chinese_font, save_figure


def plot_line_chart(data: pd.DataFrame, *, x: str, y: str, title: str) -> Path:
    configure_chinese_font()
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(data[x], data[y], marker="o", linewidth=2, color="#2563eb")
    ax.set_title(title)
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.grid(True, alpha=0.3)
    return save_figure(fig, "line")
