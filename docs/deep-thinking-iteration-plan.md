# Deep Thinking 迭代计划（2026-11-18，待批准后执行）

> 来源：对 `kacore/pipelines/deep_thinking_*` 的审查（代码与 KA-Benchmark s01 所测 `38446f4` 一致）+ s01 结果
> （deep_thinking 找到的文献最多，但证据命中最低、正确性不如 enhanced，调用是 enhanced 的约 5 倍）。
> 验证工具：KA-Benchmark 的轻量 benchmark（`kabench mini`，24 题，约 7–10 分钟一次）。
> 本文件是计划，不是已批准的执行令；每个 Phase 开工前在 `TODO.md` 登记并按 CLAUDE.md §3–§4 走。

## 0. 怎么判断一次改动

- 每个 Phase = 一个独立提交 → 跑一次 mini → 与基线运行对比（`kabench mini run --commit <sha> --baseline <run_id>`）。
- 一次只动一个变量；先纯提速，再动质量。
- 噪声：同一提交跑两次，24 题里约 3 题翻转（`36ebe71`：base1 18/24、base2 19/24）。
  - 净变好 ≥3 题，或答对持平且调用 / 耗时明显下降 → 保留；
  - 净变坏 ≥3 题 → 撤回；介于之间 → 再跑一次。
- 不往 24 题上拟合：每个改动必须先有机制上的理由；mini 只确认方向，几步做完后用全量切片（评测机）确认。
- **Phase 0：迭代开始时先在当时的 developer 最新提交上重跑两次基线。** `0c1ee58` 改了 `retrieval_orchestrator.py`
  （稠密召回的范围），工作区还有未提交的 FTS 改动，`36ebe71` 的基线不能直接对比新代码。

## 1. 各 Phase

| Phase | 改什么 | 为什么（证据） | 在 mini 上看什么 |
|---|---|---|---|
| P1 重排提速 | `evidence_session.select`：各 query 池的重排改为并发（锁只护缓存）；「补候选」去掉停用词、只给本轮新 query 补 | 每题 23–34 次重排；锁内逐池串行；`len(word) > 2` 的子串匹配让 the/what 几乎命中所有块，每轮给旧池再补一次又要重打分（`evidence_session.py:94-141`） | 答对持平；重排次数、中位耗时明显下降 |
| P2 证据稀释 | `cc81663` | P1 | 20/24、19/24（两次） | 1 / 3、0 / 3 | 24.7 / 18.2 · 28.6 / 21.8 | 264 s · 271 s | — | **撤回**降权与 18→12，只留锚点修复：稳定题 W2988278250 金标 2/2→0/2、来源 2→8 篇；第 2 轮起的 query 是 SEA 针对缺口的补检，金标常靠它们找到，「次方面 = 偏题」不成立 |
| P2 证据稀释（已试，撤回大部分） | 最终证据名额先保原问题与关键检查项，发现项 / 校验缺口只补空位；`max_final_evidence` 18 → 12；会话路径补回结构锚点保护 | 每轮每个 query 都是平等 aspect，按 aspect 归一化后各池第一名都是 1.0，补位也近似轮流分（`evidence_selection.py:96-117`、`evidence_session.py:104-167`）；会话路径从不填 `pinned_ids`（`deep_thinking_orchestrator.py:511-525`） | 证据命中上升；原答错组变对 |
| P3 收敛条件 | 关键项都满足且 SEA 判充分时，新发现的方面不再强制多跑一轮 | `converged` 要求本轮无新发现，SEA 几乎总有发现（`deep_thinking_orchestrator.py:244-249`） | 调用下降；答对不降 |
| P4 校验闭环 | 只用 `hard_missing` + `info_gaps` 补检，不用 `partial_claims`；第二稿校验不优于第一稿时保留第一稿 | `recheck_gaps = soft or hard`，soft 含答案里的断言句；第二稿总是替换第一稿（`deep_thinking_orchestrator.py:636-641`） | 对→错减少 |
| P5 单变量试验 | 各跑一次：`rerank_weight` 0.2 → 0.5（与 enhanced 同）；合成模板对短答案题不强制报告体 | 假设，需要实测（`config.py:311`、`answer_synthesis.py:124-138`） | 各自的翻转 |

## 2. 注意

- P1 的「补候选」在 `EvidenceSession` 里，enhanced 也用它：P1 那次加跑 enhanced（`--systems deep_thinking,enhanced`），确认没变差。
- 每个 Phase 跑 KA 自己的测试（`python -m pytest`、`ruff check .`）；远端操作每次单独批准。
- mini 结果只在本地 `KA-Benchmark/runs/mini/`；结论写回本文件对应 Phase 的「结果」行。

## 3. 结果

（每个 Phase 跑完后补：提交号、基线 run_id、答对数、翻转、每题调用、中位耗时、保留 / 撤回。）

| Phase | 提交 | 对照 | deep 答对 | deep 翻转（对基线） | deep 每题调用 / 重排（RQA · MHR） | deep 中位耗时 | enhanced 答对 | 结论 |
|---|---|---|---|---|---|---|---|---|
| P0 基线 1 | `ab9c749` | — | 18/24 | — | 35.7 / 28.8 · 39.8 / 32.9 | 285 s · 281 s | 21/24 | 基线（`20261118-221551_ab9c749f37ef_p0-base1`） |
| P0 基线 2 | `ab9c749` | 基线 1 | 19/24 | 2 错→对 / 1 对→错 | 35.3 / 28.4 · 37.2 / 30.3 | 318 s · 347 s | — | 噪声 3 题，与 `36ebe71` 时一致 |
| P1 重排提速 | `a24be5c` | 基线 1 | 22/24 | 4 / 0 | 26.1 / 19.4 · 28.5 / 21.6 | 214 s · 254 s | 21/24（1 / 1） | **保留**：重排 −34%、调用 −27%、中位耗时 −25% / −10%；答对未降。4 题变对里 2 题在基线 2 里也翻过（噪声题），质量提升不作结论 |

