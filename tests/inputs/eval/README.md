# 评测输入映射

评测输入仍保存在项目根目录的 `samples/` 与 `_test_paper/`，因为
`configs/eval_set.json` 直接引用这两个路径，移动会破坏 `python -m paper2code eval`。

| 用例 | 输入 |
| --- | --- |
| `01_open_source` | `samples/01_open_source` |
| `02_no_code` | `samples/02_no_code` |
| `03_pseudocode` | `samples/03_pseudocode` |
| `01_hsp70_bag2` | `_test_paper/01_hsp70_bag2` |
| `02_pbee_superlearner` | `_test_paper/02_pbee_superlearner` |
| `03_pbee_si` | `_test_paper/03_pbee_si` |

输出统一在 `outputs/eval/`。完整说明见 `tests/README.md`。
