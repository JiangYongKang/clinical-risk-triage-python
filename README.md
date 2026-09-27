# 临床风险分层与决策支持服务

基于 FastAPI 的本地服务，用内存数据模拟患者病例、决策规则与审核流程，
保证风险结论在不同入口与并发场景下一致且可追溯。

## 架构

| 模块 | 职责 |
| --- | --- |
| `app/models.py` | 病例、评估结论、规则版本等不可变数据模型与状态枚举 |
| `app/rules.py` | 规则引擎：多版本注册、纯函数评估、结论复现 |
| `app/store.py` | 线程安全内存存储：幂等提交、状态机流转、评估唯一保存 |
| `app/service.py` | 编排层：按病例加锁，并发评估归一化为唯一结论 |
| `app/api.py` | HTTP 接口：内部错误归一化映射为可区分的响应 |

## 规则版本模型

- `RuleVersion` 为不可变对象：阈值 `thresholds`、症状权重
  `symptom_weights`、分级截断 `level_cutoffs`（MEDIUM/HIGH/CRITICAL 严格递增）。
- 版本注册后**不可覆盖**（重复注册报 `RULE_CONFIG_ERROR`）。
- 每条 `Assessment` 绑定其使用的 `rule_version`；规则更新后历史结论
  不被追溯改写，按旧版本重算（`evaluate(case, "v1")`）必得一致结果。
- 配置缺失/截断非递增 → `RULE_CONFIG_ERROR`(422)；计算内部异常 →
  `RULE_EVALUATION_ERROR`(500)，均不直接透出堆栈。

## 状态流转规则

```
SUBMITTED -> EVALUATED -> REVIEWED -> CLOSED
```

- 仅允许上表跃迁，非法跃迁抛 `IllegalTransitionError`
  （HTTP 409，`ILLEGAL_TRANSITION`），响应含当前/目标状态。
- 评估成功后自动 `SUBMITTED -> EVALUATED`。
- 同一 `idempotency_key` 重复提交：载荷一致则幂等返回首次结果；
  载荷冲突则 409 `DUPLICATE_SUBMISSION`。

## 错误类型一览

| code | HTTP | 含义 |
| --- | --- | --- |
| `DUPLICATE_SUBMISSION` | 409 | 提交凭据冲突 |
| `ILLEGAL_TRANSITION` | 409 | 非法状态跃迁 |
| `CASE_NOT_FOUND` | 404 | 病例不存在 |
| `RULE_VERSION_NOT_FOUND` | 404 | 规则版本不存在 |
| `RULE_CONFIG_ERROR` | 422 | 规则配置错误 |
| `RULE_EVALUATION_ERROR` | 500 | 规则计算内部异常（已归一化） |

## 接口

- `POST /cases` `{idempotency_key, case}` 提交病例
- `POST /cases/{id}/evaluate?rule_version=v1` 评估（默认最新版本）
- `POST /cases/{id}/transitions` `{target}` 状态流转
- `GET /cases/{id}` 查询状态

## 验证方法

```bash
uv sync
uv run pytest -q -s   # -s 可查看单测日志中的输入与判定依据
uv run uvicorn main:app --port 8000
```

测试覆盖：重复提交（幂等/冲突）、非法流转、规则版本切换与旧版本
重算一致性、32 线程并发评估结论唯一、API 错误码可区分。
