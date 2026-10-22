"""MVP 导出（roadmap M5-③）：自包含 ipynb / 一键 Colab 说明 / Streamlit 小演示。

**为什么是「自包含」notebook**：本地 ``mvp/`` 目录里的文件在 Colab 上并不存在，
所以 notebook 不能只写「运行 demo/run_demo.py」——它必须把 demo 与绘图脚本**写进 notebook 自身**
（``%%writefile`` 单元格），这样在 Colab 里「全部运行」就能出结果与图。

**「一键 Colab」的诚实边界**：Colab 只能打开**已经托管**的笔记本（GitHub/GDrive）。
离线环境无法替你上传，因此这里：① 生成可上传的 ipynb；② 若配置了 ``notebook_repo``，
就给出真正的 ``colab.research.google.com/github/…`` 链接；③ 否则在 README 里写清「下载后手动上传」
——不假装有一个点了就能用的链接。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

EXPORT_DIR = "export"
NOTEBOOK = "mvp_demo.ipynb"
STREAMLIT_APP = "streamlit_app.py"
README = "README.md"

#: 写进 notebook 的文本文件后缀（二进制产物不进 notebook）
TEXT_EXTS = (".py", ".json", ".md", ".txt", ".csv")
#: 这些文件要在其它文件之后写（它们导入前两者）
LAST = ("run_demo.py", "run_figures.py")


def _cells_metadata() -> Dict[str, Any]:
    return {"kernelspec": {"name": "python3", "display_name": "Python 3"},
            "language_info": {"name": "python"}}


def _md(text: str) -> Dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": _src(text)}


def _code(text: str) -> Dict[str, Any]:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _src(text)}


def _src(text: str) -> List[str]:
    lines = text.splitlines(keepends=True)
    return lines or [""]


def _text_files(root: Path, subdirs: Sequence[str]) -> List[Path]:
    out: List[Path] = []
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(base)
            if not p.is_file():
                continue
            if "out" in rel.parts or "results" in rel.parts or "__pycache__" in rel.parts:
                continue
            if p.suffix.lower() in TEXT_EXTS:
                out.append(p.relative_to(root))
    return sorted(out, key=lambda r: (r.name in LAST, str(r)))


def notebook_cells(paper_id: str, title: str, out: Path, repo: str = "", subdir: str = "") -> List[Dict[str, Any]]:
    """构造 notebook 的单元格序列。"""
    files = _text_files(out, ("demo", "figures"))
    cells = [_md(
        "# %s · MVP demo（论文 `%s`）\n\n"
        "> 这是 paper2code 从论文里**自动生成的最小可行 demo**。它实现论文的核心机制，"
        "但用的是**玩具/合成数据**——曲线与数值只反映**定性趋势**，"
        "**不是论文里的真实数值**。要看哪些地方被简化了，读 `MVP_REPORT.md`。\n\n"
        "- 生成时间：%s\n- 自包含：下面的单元格会把 demo 与绘图脚本写进运行时目录再执行。\n"
        % (title, paper_id, datetime.now().isoformat(timespec="seconds")))]
    cells.append(_code(
        "# Colab 上需要装依赖；本地已有 numpy/matplotlib 时可跳过\n"
        "import importlib.util as _u\n"
        "if _u.find_spec('matplotlib') is None or _u.find_spec('numpy') is None:\n"
        "    !pip install -q numpy matplotlib\n"))
    if subdir:
        cells.append(_code("import os\nos.makedirs(%r, exist_ok=True)\n" % subdir))
    if files:
        cells.append(_md("## 1. 写入 demo 文件（自包含：不依赖本机路径）"))
    for rel in files:
        body = (out / rel).read_text(encoding="utf-8", errors="replace")
        target = str(rel).replace("\\", "/")
        parent = str(Path(target).parent).replace("\\", "/")
        # 注意：不要用 % 格式化拼 ipython 魔法命令（`%%` 会被吃掉一个 %，导致写不出文件）
        pre = "%%writefile " + target + "\n"
        if parent not in (".", ""):
            cells.append(_code("import os\nos.makedirs(%r, exist_ok=True)\n" % parent))
        cells.append(_code(pre + body))
    cells += [
        _md("## 2. 运行 smoke（无参数 = 小规模，几十秒）"),
        _code("!python demo/run_demo.py\n"),
        _md("## 3. 看结果（demo 写出的 `results/metrics.json`）"),
        _code(
            "import json, pathlib\n"
            "mp = pathlib.Path('demo/results/metrics.json')\n"
            "data = json.loads(mp.read_text(encoding='utf-8')) if mp.exists() else {}\n"
            "print(json.dumps(data.get('metrics', {}), ensure_ascii=False, indent=2))\n"
            "for name, c in (data.get('curves') or {}).items():\n"
            "    print(name, '=', len(c.get('y') or []), '点')\n"),
        _md("## 4. 重绘论文里的图（需要 matplotlib）"),
        _code("import os\nos.makedirs('figures/out', exist_ok=True)\n!python figures/run_figures.py\n"),
        _code(
            "from IPython.display import display, Image\nimport pathlib\n"
            "for p in sorted(pathlib.Path('figures/out').glob('*.png')):\n"
            "    print(p.name); display(Image(filename=str(p)))\n"),
        _md("> 图上都带 `MVP toy data – NOT the paper's results` 水印：只反映定性趋势。"),
    ]
    if repo:
        link = "https://colab.research.google.com/github/%s/blob/main/%s" % (repo, subdir or "mvp_demo.ipynb")
        cells.insert(1, _md("[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](%s)" % link))
    return cells


def to_notebook(out: Path | str, paper_id: str, title: str = "", repo: str = "",
                subdir: str = "") -> Path:
    """把 mvp 目录导出成自包含的 ``export/mvp_demo.ipynb``。"""
    out = Path(out)
    dest_dir = out / EXPORT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    nb = {"cells": notebook_cells(paper_id, title or paper_id, out, repo=repo, subdir=subdir),
          "metadata": _cells_metadata(), "nbformat": 4, "nbformat_minor": 5}
    p = dest_dir / NOTEBOOK
    p.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


STREAMLIT_TEMPLATE = '''"""MVP 小演示（Streamlit）——由 paper2code 自动生成（roadmap M5-③）。

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
PAPER_ID = %(paper_id)r
TITLE = %(title)r


def main() -> None:
    st.set_page_config(page_title=TITLE, layout="wide")
    st.title(TITLE)
    st.caption("论文 `%%s` · 由 paper2code 自动生成的 MVP（玩具数据，只反映定性趋势）" %% PAPER_ID)

    metrics = HERE / "demo" / "results" / "metrics.json"
    if not metrics.exists():
        st.warning("还没有跑过 demo。先在仓库根执行：python -m paper2code mvp <论文>")
        return
    data = json.loads(metrics.read_text(encoding="utf-8"))

    st.subheader("指标")
    st.json(data.get("metrics") or {})
    for name, curve in (data.get("curves") or {}).items():
        st.subheader("曲线：%%s" %% name)
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
            cols[i %% len(cols)].image(str(p), caption=p.name)

    report = HERE / "MVP_REPORT.md"
    if report.exists():
        with st.expander("MVP 报告（诚实边界在这里）"):
            st.markdown(report.read_text(encoding="utf-8"))


main()
'''


def to_streamlit(out: Path | str, paper_id: str, title: str = "") -> Path:
    """生成 ``export/streamlit_app.py``（只读已有产物，不重新执行代码）。"""
    out = Path(out)
    dest_dir = out / EXPORT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / STREAMLIT_APP
    p.write_text(STREAMLIT_TEMPLATE % {"paper_id": paper_id, "title": title or paper_id},
                 encoding="utf-8")
    return p


def _readme(paper_id: str, title: str, repo: str, nb_name: str, has_notebook: bool,
            has_streamlit: bool, versions: Optional[Dict[str, Any]] = None) -> str:
    L = ["# %s · MVP 导出（论文 `%s`）\n" % (title or paper_id, paper_id),
         "本目录是 `outputs/single/%s/mvp/export/`，由 `python -m paper2code export %s` 生成。\n" % (paper_id, paper_id)]
    if has_notebook:
        L += ["## 1. 在 Colab / Jupyter 里跑", "",
              "- 文件：`%s`（**自包含**：demo 与绘图脚本都写在 notebook 单元格里）。" % nb_name]
        if repo:
            L.append("- 一键打开：把本文件提交到 `%s` 的 `main` 分支后，"
                     "可用 `https://colab.research.google.com/github/%s/blob/main/<路径>` 直接打开。" % (repo, repo))
        else:
            L += ["- **一键 Colab 链接需要先把 notebook 托管出去**（GitHub / Google Drive）——"
                  "本机离线环境无法替你上传，所以这里不给一个点了会 404 的假链接。",
                  "  1. 把 `%s` 上传到你的 GitHub 仓库或 Google Drive；" % nb_name,
                  "  2. Colab → 文件 → 打开笔记本 → 上传 / GitHub；",
                  "  3. 想自动带上链接：设置 `P2C_NOTEBOOK_REPO=<owner>/<repo>` 后重新导出。"]
    if has_streamlit:
        L += ["", "## 2. 本地小演示（Streamlit）", "",
              "```bash", "pip install streamlit", "streamlit run export/streamlit_app.py", "```",
              "它**只读**已跑出来的产物（`demo/results/metrics.json`、`figures/out/*.png`），"
              "不重新执行代码、不联网。"]
    vs = (versions or {}).get("live") or {}
    if vs:
        L += ["", "## 3. 当前版本", "",
              "- v%s ｜ 生成方式 `%s` ｜ 本次指令：%s" % (vs.get("n", 1), vs.get("generator", "-"),
                                                vs.get("instruction") or "（首次生成）"),
              "- demo 运行：%s" % ("通过" if vs.get("demo_ok") else
                                   ("未执行" if vs.get("demo_ok") is None else "失败"))]
    L += ["", "## 4. 诚实边界", "",
          "- notebook 里的图带 `MVP toy data – NOT the paper's results` 水印：**只反映定性趋势**。",
          "- demo 用玩具/合成数据实现论文的核心机制；相对论文简化了什么，见 `../MVP_REPORT.md`。",
          "- Colab 上的依赖（numpy/matplotlib）由 notebook 第一个单元格自动安装。"]
    return "\n".join(L) + "\n"


def export_mvp(out: Path | str, paper_id: str, title: str = "", repo: str = "") -> Dict[str, Any]:
    """一次导出 notebook + Streamlit 演示 + README，返回产物路径字典。"""
    from . import iterate

    out = Path(out)
    if not out.exists():
        return {"ok": False, "reason": "还没有 MVP 产物（先跑一次 mvp）", "dir": str(out / EXPORT_DIR)}
    nb = to_notebook(out, paper_id, title=title, repo=repo, subdir="")
    app = to_streamlit(out, paper_id, title=title)
    versions = iterate.summarize(out)
    rel_nb = str(nb.relative_to(out)).replace("\\", "/")
    rel_app = str(app.relative_to(out)).replace("\\", "/")
    text = _readme(paper_id, title, repo, rel_nb, nb.exists(), app.exists(), versions)
    (out / EXPORT_DIR / README).write_text(text, encoding="utf-8")
    return {"ok": True, "dir": str(out / EXPORT_DIR),
            "notebook": str(nb), "streamlit": str(app),
            "readme": str(out / EXPORT_DIR / README),
            "notebook_rel": rel_nb, "streamlit_rel": rel_app,
            "readme_rel": str((out / EXPORT_DIR / README).relative_to(out)).replace("\\", "/"),
            "colab_url": ("https://colab.research.google.com/github/%s/blob/main/%s" % (repo, rel_nb)
                          if repo else ""),
            "n_cells": len(json.loads(nb.read_text(encoding="utf-8"))["cells"])}
