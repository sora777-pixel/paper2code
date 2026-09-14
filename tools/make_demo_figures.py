"""生成示例论文包里随文附带的插图（SVG）。

示例论文包里的 ``figures/*.svg`` 相当于「论文自带的图」。有了它们，以下链路
才能被真实地走通：

* ``ingest.extract.extract_figures`` 把 ``Figure N.`` 图题与图片文件关联起来；
* 讲解课件会把原文图直接嵌进对应幻灯片；
* 复现环节则用表格数据**重绘**同一张图，两者可并排目视比对。

用法::

    python tools/make_demo_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paper2code.reproduce.charting import ChartSpec, build_svg  # noqa: E402

SAMPLE_01 = ROOT / "samples" / "01_open_source" / "figures"
SAMPLE_02 = ROOT / "samples" / "02_no_code" / "figures"
SAMPLE_03 = ROOT / "samples" / "03_pseudocode" / "figures"


def write(spec: ChartSpec, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_svg(spec), encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)}")


def main() -> None:
    write(
        ChartSpec(
            kind="grouped_bar",
            title="Figure 1. Clustering performance on StackOverflow",
            xlabel="Method",
            ylabel="Score",
            categories=["K-Means", "NMF", "BERTopic", "Ours"],
            series=[
                {"name": "Accuracy", "values": [62.4, 66.1, 71.5, 78.3]},
                {"name": "NMI", "values": [51.8, 55.3, 61.2, 68.7]},
                {"name": "ARI", "values": [44.1, 47.9, 53.6, 61.4]},
            ],
            note="原文插图（示例数据），水平越高越好；复现阶段会用同一份数据重绘。",
        ),
        SAMPLE_01 / "figure1.svg",
    )

    write(
        ChartSpec(
            kind="grouped_bar",
            title="Figure 1. Request share versus byte share by content class",
            xlabel="Content class",
            ylabel="Share",
            categories=["Static", "Images", "Video", "API"],
            series=[
                {"name": "Request share", "values": [0.468, 0.274, 0.071, 0.187]},
                {"name": "Byte share", "values": [0.121, 0.124, 0.684, 0.071]},
            ],
            note="原文插图（示例数据）：视频只占 7.1% 的请求，却占 68.4% 的字节。",
        ),
        SAMPLE_02 / "figure1.svg",
    )

    write(
        ChartSpec(
            kind="grouped_bar",
            title="Figure 1. Throughput of STREAMTOPK versus the naive baseline",
            xlabel="Stream distribution",
            ylabel="Million elements / second",
            categories=["Uniform", "Log-normal", "Bursty", "Baseline"],
            series=[{"name": "Throughput", "values": [41.3, 38.7, 35.1, 12.4]}],
            note="原文插图（示例数据）：快速路径带来约 3 倍加速。",
        ),
        SAMPLE_03 / "figure1.svg",
    )


if __name__ == "__main__":
    main()
