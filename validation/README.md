# validation · 验证产物落点

本目录放**端到端验证**的产物，结构与字段定义见
`docs/spec/端到端验证计划-v0.md` §8（验证输出）与 §4（六道门槛）。

```text
validation/{validation_id}/
  manifest.json            本批验证的 manifest（含三份真源哈希）
  preregistration.json     预注册（样本计划、随机顺序、预算、阈值、停止规则）
  state-inventory.json     状态对象清点（决定快照档位）
  fixture-manifest.json    受控夹具 manifest（正确信息 / 无信息 / 错误信息）
  case-results.jsonl       逐案例结果（预期、观察、检测事件、处置事件、受影响对象、证据、恢复结果、复核人）
  evidence-manifest.json   证据 manifest 与依赖关系
  baseline-report.json     基线报告
  fault-injection/         三类故障注入（判分器错误 / 状态恢复失败 / 证据缺失篡改）
  review.json              独立复核记录
  validation-report.md     验证报告（列通过 / 失败 / 阻塞，不把未运行算通过）
```

## 现状

**本目录当前为空（只有 `.gitkeep`）。** 本仓是 MVP，尚未执行端到端验证；
因此也**没有经过验证的正式成绩**，正式能力榜不开。

## 规矩（真源写死，不可放宽）

- 注入只在**独立、可销毁的验证副本**中进行，先登记 manifest 和备份；**禁止删除正式证据原件**。
- 注入器独立于检测器，不得给检测器一个「这次有故障」的作弊标记。
- 每个案例记录：预期、观察、检测事件、处置事件、受影响对象、证据、恢复结果、复核人。
- 通过规则：本批已登记必测案例全部通过；不存在未处置的阻塞问题；独立复核完成。
  **通过只证明本批范围，不宣称系统能抓到所有未知错误。**
- 不能为了通过测试降低门槛；也不能因为「已经冻结」就保留已知错误。
