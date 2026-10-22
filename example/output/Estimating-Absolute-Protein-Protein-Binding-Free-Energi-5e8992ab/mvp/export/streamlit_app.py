"""MVP 小演示（Streamlit）——由 paper2code 自动生成（roadmap M5-③）。

用法::

    pip install streamlit
    streamlit run streamlit_app.py

它**只读**本目录里 demo 已经跑出来的产物（`demo/results/metrics.json` 与 `figures/out/*.png`），
不重新执行代码、不联网。要重新生成，用 CLI 再跑一次 paper2code。
"""
import json
from pathlib import Path

import streamlit as st

HERE = Path(__file__).resolve().parent.parent      # mvp/
PAPER_ID = 'Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab'
TITLE = 'Estimating Absolute Protein−Protein Binding Free Energies by a Super Learner Model'


def main() -> None:
    st.set_page_config(page_title=TITLE, layout="wide")
    st.title(TITLE)
    st.caption("论文 `%s` · 由 paper2code 自动生成的 MVP（玩具数据，只反映定性趋势）" % PAPER_ID)

    metrics = HERE / "demo" / "results" / "metrics.json"
    if not metrics.exists():
        st.warning("还没有跑过 demo。先在仓库根执行：python -m paper2code mvp <论文>")
        return
    data = json.loads(metrics.read_text(encoding="utf-8"))

    st.subheader("指标")
    st.json(data.get("metrics") or {})
    for name, curve in (data.get("curves") or {}).items():
        st.subheader("曲线：%s" % name)
        st.line_chart({"x": curve.get("x"), "y": curve.get("y")}, x="x", y="y")
    if data.get("ablation"):
        st.subheader("消融")
        ab = data["ablation"]
        st.bar_chart({"variant": ab.get("names"), "value": ab.get("values")}, x="variant", y="value")

    figs = sorted((HERE / "figures" / "out").glob("*.png"))
    if figs:
        st.subheader("重绘的图")
        cols = st.columns(min(3, len(figs)))
        for i, p in enumerate(figs):
            cols[i % len(cols)].image(str(p), caption=p.name)

    report = HERE / "MVP_REPORT.md"
    if report.exists():
        with st.expander("MVP 报告（诚实边界在这里）"):
            st.markdown(report.read_text(encoding="utf-8"))


main()
