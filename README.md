# 临床风险分层与决策支持服务

基于 FastAPI 的本地服务，用内存数据模拟患者病例、决策规则与审核流程，
保证风险结论在不同入口与并发场景下一致且可追溯。

## 架构

| 模块 | 职责 |
| --- | --- |
| `app/models.py` | 病例、评估结论、规则版本等不可变数据模型与状态枚举 |
| `app/rules.py` | 规则引擎：多版本注册、纯函数评估、结论复现 |
| `app/store.py` | 线程安全内存存储：幂等提交、状态机流转、评估唯一保存、只增不改的评估轨迹 |
| `app/service.py` | 编排层：按病例加锁，并发评估/重算归一化，结论唯一 |
| `app/api.py` | HTTP 接口：内部错误归一化映射为可区分的响应 |

## 规则版本模型

- `RuleVersion` 为不可变对象：阈值 `thresholds`、症状权重
  `symptom_weights`、分级截断 `level_cutoffs`（MEDIUM/HIGH/CRITICAL 严格递增）。
- 版本注册后**不可覆盖**（重复注册报 `RULE_CONFIG_ERROR`）。
- 每条 `Assessment` 绑定其使用的 `rule_version`；规则更新后历史结论
  不被追溯改写，按旧版本重算（`evaluate(case, "v1")`）必得一致结果。
- 配置缺失/截断非递增 → `RULE_CONFIG_ERROR`(422)；计算内部异常 →
  `RULE_EVALUATION_ERROR`(500)，均不直接透出堆栈。

## 指定版本重算（只读复核）

- `POST /cases/{id}/reassess` 请求体 `{"rule_version": "v1"}`：按指定
  规则版本对病例重新计算，用于复审时核对当时的判断。
- **只读**：不覆盖病例已绑定的正式结论，不改变病例状态；每次重算仅向
  评估轨迹追加一条 `REASSESS` 记录。
- 响应显式标注版本关系：`rule_version`（本次重算版本）、
  `bound_rule_version`（病例绑定版本）、`version_match`（是否一致）。
  - 指定版本与绑定版本**不一致**：正常返回重算结论，`version_match=false`；
  - 指定版本**未注册**：404 `RULE_VERSION_NOT_FOUND`。
  两种情形可清楚区分，传入的版本不会被静默忽略。
- 前提：病例须已完成正式评估，否则 404 `CASE_NOT_FOUND`。

## 评估轨迹

- `GET /cases/{id}/assessments` 返回该病例完整的评估轨迹，按发生顺序
  排列：每次正式评估（`EVALUATE`）与每次重算（`REASSESS`）各一条记录，
  含 `seq`、`rule_version`、`evaluated_at`、`score`、`risk_level`、
  `rationale`。
- 轨迹**只增不改**：后续上线新规则版本或重算，都不会冲掉或改写早先记录；
  任意一条记录都能说清是哪版规则、在什么时间得出的。
- 并发保证：重算按病例加锁、轨迹追加串行化；多人同时用不同版本重算
  同一病例时，每个版本的结论各自自洽唯一、互不覆盖，同一版本重复算
  结果一致。

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

所有错误（含请求体字段缺失/类型错误）统一为
`{"error": {"code": ..., "message": ...}}` 结构，不透出框架底层校验细节。

| code | HTTP | 含义 |
| --- | --- | --- |
| `DUPLICATE_SUBMISSION` | 409 | 提交凭据冲突 |
| `ILLEGAL_TRANSITION` | 409 | 非法状态跃迁 |
| `CASE_NOT_FOUND` | 404 | 病例不存在（或尚无评估结论） |
| `RULE_VERSION_NOT_FOUND` | 404 | 规则版本未注册 |
| `INVALID_REQUEST` | 422 | 请求体字段缺失/类型错误（已归一化） |
| `RULE_CONFIG_ERROR` | 422 | 规则配置错误 |
| `RULE_EVALUATION_ERROR` | 500 | 规则计算内部异常（已归一化） |

## 接口

- `POST /cases` `{idempotency_key, case}` 提交病例
- `POST /cases/{id}/evaluate?rule_version=v1` 评估（默认最新版本）
- `POST /cases/{id}/reassess` `{"rule_version": "v1"}` 指定版本只读重算
- `GET /cases/{id}/assessments` 查询完整评估轨迹
- `POST /cases/{id}/transitions` `{target}` 状态流转
- `GET /cases/{id}` 查询状态

## 验证方法

```bash
uv sync
uv run pytest -q -s   # -s 可查看单测日志中的输入与判定依据
uv run uvicorn main:app --port 8000
```

本地手工验证新入口：

```bash
curl -X POST localhost:8000/cases -H 'Content-Type: application/json' -d '{
  "idempotency_key": "k1",
  "case": {"case_id": "c1", "age": 70, "systolic_bp": 190, "heart_rate": 130,
           "temperature_c": 39.0, "spo2": 90, "symptoms": ["chest_pain"]}}'
curl -X POST 'localhost:8000/cases/c1/evaluate?rule_version=v1'
curl -X POST localhost:8000/cases/c1/reassess -H 'Content-Type: application/json' \
  -d '{"rule_version": "v1"}'
curl localhost:8000/cases/c1/assessments
```

测试覆盖：重复提交（幂等/冲突）、非法流转、规则版本切换与旧版本
重算一致性、指定版本重算的复现一致与版本不一致标注、评估轨迹有序且
不被追溯改写、坏输入统一报错、32 线程并发评估结论唯一、32 线程混合
版本并发重算各版本结论唯一、API 错误码可区分。
