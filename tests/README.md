# 测试资产目录

> 测试输入统一放在 `tests/inputs/`，测试输出统一留在 `outputs/`，两者不混放。
> `tests/inputs/single/` 与 `tests/inputs/batch/` 已在 `.gitignore` 中排除，不会上传 GitHub。

## 目录映射

| 测试类型 | 输入目录 | 输出目录 | 数量 |
| --- | --- | --- | ---: |
| 单篇 | `tests/inputs/single/<paper_id>/` | `outputs/single/<paper_id>/` | 2 |
| 批量 | `tests/inputs/batch/<paper_id>.pdf` | `outputs/batch/patch2/<paper_id>/` | 19 |
| 评测 | `samples/`、`_test_paper/`（保持在原路径） | `outputs/eval/` | 6 |

说明：评测输入 `samples/` 与 `_test_paper/` 被 `configs/eval_set.json` 直接引用，
因此没有物理移动到 `tests/inputs/eval/`；该目录只放映射说明，避免破坏 `python -m paper2code eval`。

## 单篇测试

两篇当前单篇测试：

- `tests/inputs/single/Disruption-of-Hsp70.14-BAG2-Protein-Protein-interaction-0f68d6a2/`
- `tests/inputs/single/Estimating-Absolute-Protein-Protein-Binding-Free-Energies-by-a-Super-Learner-Model/`

运行示例：

```bat
python -m paper2code run tests\inputs\single\Estimating-Absolute-Protein-Protein-Binding-Free-Energies-by-a-Super-Learner-Model --llm offline
```

产物写入 `outputs/single/<paper_id>/`。

## 批量测试

19 篇源 PDF 位于 `tests/inputs/batch/`，与 `outputs/batch/patch2/` 下的 19 个产物目录一一对应。

```bat
python -m paper2code batch tests\inputs\batch --llm offline -o outputs\batch\patch2
```

产物写入 `outputs/batch/patch2/<paper_id>/`。

## 评测测试

6 个评测用例来自：

- `samples/01_open_source`
- `samples/02_no_code`
- `samples/03_pseudocode`
- `_test_paper/01_hsp70_bag2`
- `_test_paper/02_pbee_superlearner`
- `_test_paper/03_pbee_si`

```bat
python -m paper2code eval
```

产物写入 `outputs/eval/`。
