# test-map 实验：Python 图表可视化

## 安装

```bash
cd test_project
pip install -r requirements.txt
```

## 运行

```bash
python demo.py
```

图表将保存到 `output/`，文件名格式为 `{chart_type}_{timestamp}.png`（150 DPI）。

## 结构

```
test_project/
├── charts/           # 折线图、柱状图、散点图模块
├── data/             # 示例 CSV 数据
├── demo.py           # 统一入口
├── output/           # 生成的 PNG（运行后产生）
└── requirements.txt
```

## 图表类型

| 模块 | 说明 |
|------|------|
| `charts/line_chart.py` | 折线图 |
| `charts/bar_chart.py` | 柱状图 |
| `charts/scatter_chart.py` | 散点图 |

无 GUI 环境使用 matplotlib `Agg` 后端，可直接在服务器或 Docker 中运行。
