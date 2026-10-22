#!/usr/bin/env bash
# ============================================================
# 由 paper2code 自动生成的复现脚本（情形 A：有开源代码）
# 目标：在单机环境下，用仓库自身代码重跑论文中的表格与图表
# ============================================================
set -euo pipefail

REPO_DIR="${REPO_DIR:-E:\WorkBuddy\paper2code\outputs\single\Estimating-Absolute-Protein-Protein-Binding-Free-Energi-5e8992ab\reproduce\repo}"
WORK_DIR="${WORK_DIR:-$PWD/.p2c_repro}"

echo "[1/5] 创建工作目录 $WORK_DIR"
mkdir -p "$WORK_DIR"

echo "[2/5] 创建隔离虚拟环境（避免污染系统环境）"
python -m venv "$WORK_DIR/venv" 2>/dev/null || echo "  venv 已存在"
# shellcheck disable=SC1091
source "$WORK_DIR/venv/bin/activate" 2>/dev/null || source "$WORK_DIR/venv/Scripts/activate"

echo "[3/5] 安装依赖"
if [ -f "$REPO_DIR/environment.yml" ]; then
  pip install -r "$REPO_DIR/environment.yml"
else
  echo "  未找到 environment.yml，跳过（请人工确认依赖）"
fi

echo "[4/5] 固定随机种子以保证可复现"
export PYTHONHASHSEED=0
export P2C_SEED=0
export MPLBACKEND=Agg
export PYTHONIOENCODING=utf-8
export OMP_NUM_THREADS=4

echo "[5/5] 执行复现入口"
cd "$REPO_DIR"
python pbee.py "$@"

echo "完成。仓库内的数据文件（前若干）： 无"
echo "把产出的 results/*.csv 放回 paper2code 的 outputs/single/<paper>/reproduce/ 即可自动对标。"
