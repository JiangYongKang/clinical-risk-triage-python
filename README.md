# 本地临床风险分层与决策支持服务

用本地内存数据模拟患者病例、决策规则版本与审核流程。风险结论在不同入口（服务层 / HTTP）
与并发场景下保持一致、可解释、可追溯，历史结论不会因规则更新而被改写。

## 运行

```bash
uv sync
uv run python main.py                 # 启动 HTTP 服务（127.0.0.1:8000）
uv run pytest                         # 运行测试（默认打印输入与判定依据日志）
```

## 模块结构

| 文件 | 职责 |
| --- | --- |
| `app/models.py` | 领域模型：病例、规则版本、评估结论、审核事件 |
| `app/rules.py` | 多版本规则注册表（不可变，只增不改） |
| `app/engine.py` | 无状态规则引擎：打分、分级、判定依据；内部异常类型 |
| `app/repository.py` | 内存病例仓储 + 每病例锁 + 提交凭据索引 |
| `app/service.py` | 领域服务：提交、评估、状态流转、旧版本重算、审核轨迹 |
| `app/errors.py` | 归一化错误类型与内部异常映射 |
| `app/api.py` | FastAPI 入口与 HTTP 异常归一化 |

## 规则版本模型

- 每个版本是一个**不可变**的 `RuleSet`（`frozen` dataclass），含：
  - `version`：版本号（当前发布 `v1.0`、`v2.0`）；
  - `thresholds`：MEDIUM / HIGH / CRITICAL 三级分数线；
  - `factors`：风险因子列表（因子编码、说明、权重）。
- 注册表 `RULE_VERSIONS` **只增不改**；评估结果 `RiskAssessment` 绑定其使用的 `rule_version`。
- 版本差异示例（同患者 `{age:70, spo2:90}`）：
  - `v1.0`：阈值 `(3,6,9)`，低血氧权重 3 → 总分 5 → **MEDIUM**
  - `v2.0`：阈值 `(2,5,8)`，低血氧权重 4，新增 80 岁因子 → 总分 6 → **HIGH**
- **历史不被追溯改写**：规则更新后，已绑定的结论保持原版本；
  `POST /cases/{id}/recompute` 按任意已发布版本只读重算，不覆盖绑定结论、不改状态。
- 引擎无状态：同一患者 + 同一规则版本，任何时刻重算结果一致（`computed_at` 时间戳除外）。

判定依据（`reasons`）逐条列出命中因子及权重，并给出总分、阈值与最终等级；
评估动作同时写入病例的审核轨迹 `audit`。

## 状态流转规则

```
SUBMITTED ──assess──▶ TRIAGED ──start_review──▶ REVIEWING
                         ▲                          ├── confirm ──▶ CONFIRMED(终态)
                         └────── send_back ─────────├── dismiss ──▶ DISMISSED(终态)
```

- `SUBMITTED` 必须先评估才能进入审核；未分诊直接 `start_review` 返回 `CASE_NOT_TRIAGED`。
- 非法跃迁、未知动作统一返回 `INVALID_TRANSITION`，消息中说明要求的源状态、
  当前状态及当前允许的动作，失败原因可区分。
- `CONFIRMED` / `DISMISSED` 为终态，任何继续流转均被拒绝。
- 已 `TRIAGED` 的病例在评估入口换用其他规则版本会被拒绝（防止覆盖历史），
  版本复现请走 `recompute`。

## 并发一致性

- 每个病例一把锁（`CaseRepository.lock_for`）：同一病例的并发评估/流转串行化，
  不同病例互不阻塞；全局锁仅保护仓储结构、凭据索引与锁表。
- 相同 `idempotency_key` 的并发提交：只有一次成功，其余返回 `DUPLICATE_SUBMISSION`，
  不产生重复病例与重复评估。
- 同一病例并发评估（16/32 线程）：结论唯一且完全相等，`history` 仅一条。

## 错误归一化

对外只暴露 `TriageError(code, message)`，不向接口透出内部堆栈：

| code | HTTP | 场景 |
| --- | --- | --- |
| `INVALID_PAYLOAD` | 422 | 入参缺失/类型错误 |
| `DUPLICATE_SUBMISSION` | 409 | 提交凭据重复 |
| `INVALID_TRANSITION` | 409 | 非法状态跃迁 / 未知动作 / 评估入口换版本 |
| `CASE_NOT_TRIAGED` | 409 | 未分诊即送审 |
| `CASE_NOT_FOUND` / `RULE_VERSION_NOT_FOUND` | 404 | 资源不存在 |
| `RULE_CONFIG_ERROR` | 500 | 规则引用未知因子、阈值非法（配置错误） |
| `ENGINE_INTERNAL_ERROR` | 500 | 引擎内部异常（细节被吞掉，不透出） |

## HTTP 接口

| 方法与路径 | 说明 |
| --- | --- |
| `POST /cases` | 提交病例，body: `{"patient": {...}, "idempotency_key": "..."}` |
| `GET /cases/{id}` | 查询病例快照（含绑定结论、历史、审核轨迹） |
| `POST /cases/{id}/assess` | 评估，body: `{"rule_version": "v2.0"}`（可空，默认最新版） |
| `POST /cases/{id}/recompute` | 按指定版本只读重算，body: `{"rule_version": "v1.0"}` |
| `POST /cases/{id}/transitions` | 审核流转，body: `{"action": "start_review/confirm/dismiss/send_back"}` |
| `GET /rules/versions` | 已发布规则版本列表 |

### 快速验证

```bash
curl -s -X POST localhost:8000/cases -H 'content-type: application/json' \
  -d '{"patient":{"age":70,"spo2":90},"idempotency_key":"demo-1"}'
# 用返回的 case_id：
curl -s -X POST localhost:8000/cases/<case_id>/assess -d '{}' \
  -H 'content-type: application/json'
curl -s -X POST localhost:8000/cases/<case_id>/recompute \
  -d '{"rule_version":"v1.0"}' -H 'content-type: application/json'
```

## 测试与验证方法

```bash
uv run pytest                    # 全量（默认 --log-cli，打印输入与判定依据）
uv run pytest tests/test_concurrency.py   # 仅并发场景
uv run pytest tests/test_errors.py        # 仅错误归一化
```

覆盖场景：

- **重复提交**：相同凭据串行/并发提交，只产生一份病例与一条结论；
- **重复评估**：同版本重复评估幂等；评估入口换版本被拒绝；
- **非法流转**：未分诊送审、源状态不匹配、未知动作、终态再流转，原因均可区分；
- **规则版本切换**：v1/v2 结论不同；旧版本重算可复现且不覆盖绑定结论；
- **并发评估**：多线程同病例评估结论一致、历史不重复；并发流转仅一次成功；
- **错误归一化**：未知因子/阈值非法 → `RULE_CONFIG_ERROR`；
  未知内部异常 → `ENGINE_INTERNAL_ERROR` 且不泄漏内部细节。
