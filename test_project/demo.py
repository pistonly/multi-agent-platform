#!/usr/bin/env python3
"""Generate sample charts and save PNG files to output/."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from charts import plot_bar_chart, plot_line_chart, plot_scatter_chart
from charts._utils import project_root


def load_sample_data() -> pd.DataFrame:
    csv_path = project_root() / "data" / "sample_sales.csv"
    return pd.read_csv(csv_path)


def main() -> None:
    data = load_sample_data()
    results: list[Path] = []

    results.append(
        plot_line_chart(data, x="month", y="sales", title="月度销售额趋势（折线图）")
    )
    results.append(
        plot_bar_chart(data, x="month", y="sales", title="月度销售额对比（柱状图）")
    )
    results.append(
        plot_scatter_chart(
            data,
            x="marketing_spend",
            y="conversion_rate",
            title="营销投入与转化率（散点图）",
        )
    )

    print("Generated charts:")
    for path in results:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
