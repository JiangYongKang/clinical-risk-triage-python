# 临床风险分层与决策支持服务

基于 FastAPI 的本地服务，用内存数据模拟患者病例、决策规则与审核流程，
保证风险结论在不同入口与并发场景下一致且可追溯。

## 架构

| 模块 | 职责 |
| --- | --- |
| `app/models.py` | 病例、评估结论、规则版本、轨迹记录等不可变数据模型与状态枚举 |
| `app/rules.py` | 规则引擎：多版本注册、纯函数评估、结论复现 |
| `app/store.py` | 线程安全内存存储：幂等提交、状态机流转、评估唯一保存、轨迹追加 |
| `app/service.py` | 编排层：按病例加锁，并发评估/重算归一化，只读复现 |
| `app/api.py` | HTTP 接口：内部错误与请求校验错误归一化为统一错误结构 |

## 规则版本模型

- `RuleVersion` 为不可变对象：阈值 `thresholds`、症状权重
  `symptom_weights`、分级截断 `level_cutoffs`（MEDIUM/HIGH/CRITICAL 严格递增）。
- 版本注册后**不可覆盖**（重复注册报 `RULE_CONFIG_ERROR`）。
- 每条 `Assessment` 绑定其使用的 `rule_version`；规则更新后历史结论
  不被追溯改写，按旧版本重算（`evaluate(case, "v1")`）必得一致结果。
- 配置缺失/截断非递增 → `RULE_CONFIG_ERROR`(422)；计算内部异常 →
  `RULE_EVALUATION_ERROR`(500)，均不直接透出堆栈。

## 指定版本重算与评估轨迹

- `POST /cases/{id}/reassess?rule_version=v1`：**只读**重算入口，用于复审
  核对。按指定规则版本重新计算，返回的风险等级、分数、判定依据与病例
  当初绑定的结论完全一致；**不会覆盖**病例已绑定的评估结论，也不改变
  病例状态。
  - 指定版本未登记 → 404 `RULE_VERSION_NOT_FOUND`；
  - 指定版本已登记但与病例绑定版本不一致 → 409 `RULE_VERSION_MISMATCH`
    （响应中区分 `bound`/`requested`）；
  - 病例尚未评估 → 404 `CASE_NOT_FOUND`。
- `GET /cases/{id}/assessments`：按发生顺序返回完整评估轨迹。每次正式
  评估（`EVALUATE`）与每次重算（`REASSESS`）各记录一条，含规则版本、
  时间、分数、风险等级与判定依据。
- 轨迹**只增不改**：后续上线更新的规则版本不会冲掉或改写历史记录；
  同一 (病例, 来源, 规则版本) 的记录去重，并发下同版本重复重算只会
  留下一条记录且结论唯一。
- 并发语义：多人同时用不同版本重算同一病例时，按病例加锁串行化，
  每份结论各自自洽、互不覆盖；失败的重算（版本不一致/未登记）不会
  在轨迹中留痕。

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
| `RULE_VERSION_MISMATCH` | 409 | 重算版本与病例绑定版本不一致 |
| `CASE_NOT_FOUND` | 404 | 病例不存在（或尚无评估结论） |
| `RULE_VERSION_NOT_FOUND` | 404 | 规则版本不存在 |
| `INVALID_REQUEST` | 422 | 请求体字段缺失/类型错误（已归一化，不透出框架原始校验细节） |
| `RULE_CONFIG_ERROR` | 422 | 规则配置错误 |
| `RULE_EVALUATION_ERROR` | 500 | 规则计算内部异常（已归一化） |

所有错误响应统一为 `{"error": {"code": ..., "message": ...}}` 结构。

## 接口

- `POST /cases` `{idempotency_key, case}` 提交病例
- `POST /cases/{id}/evaluate?rule_version=v1` 评估（默认最新版本）
- `POST /cases/{id}/reassess?rule_version=v1` 按指定版本只读重算（复现绑定结论）
- `GET /cases/{id}/assessments` 查询完整评估轨迹
- `POST /cases/{id}/transitions` `{target}` 状态流转
- `GET /cases/{id}` 查询状态

## 兼容边界

- 既有接口（提交/评估/流转/查询）的入参、出参与错误码均保持不变，
  老调用方无需修改。
- 评估结论在首次评估时绑定当时的规则版本，之后不可被重算或新规则
  版本改写；重算只是只读复现并追加轨迹。
- 轨迹记录为追加式：只允许新增，不提供修改/删除入口。

## 验证方法

```bash
uv sync
uv run pytest -q -s   # -s 可查看单测日志中的输入与判定依据
uv run uvicorn main:app --port 8000
```

测试覆盖：重复提交（幂等/冲突）、非法流转、规则版本切换与旧版本
重算一致性、32 线程并发评估结论唯一、指定版本只读重算复现、版本
不一致/未登记的可区分报错、轨迹顺序与不被新规则改写、坏输入统一
错误结构、混合版本并发重算结论唯一、API 错误码可区分。

本地手动验证新入口：

```bash
uv run uvicorn main:app --port 8000
curl -X POST localhost:8000/cases -H 'content-type: application/json' -d \
  '{"idempotency_key":"k1","case":{"case_id":"c1","age":70,"systolic_bp":190,"heart_rate":130,"temperature_c":39.0,"spo2":90,"symptoms":["chest_pain"]}}'
curl -X POST 'localhost:8000/cases/c1/evaluate?rule_version=v1'
curl -X POST 'localhost:8000/cases/c1/reassess?rule_version=v1'   # 只读复现
curl localhost:8000/cases/c1/assessments                          # 完整轨迹
```
