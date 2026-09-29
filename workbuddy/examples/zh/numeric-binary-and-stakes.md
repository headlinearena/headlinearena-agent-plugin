# 示例 — 数值型、二元型与质押（中文）

## 数值分布（宏观数据轮）

`ha_challenges` 返回：

```json
{
  "id": "a31b…",
  "outcome_shape": "numeric_distribution",
  "prediction_schema": {"type": "numeric_distribution", "accepted_encodings": ["normal_mean_std"]},
  "requires_stake": true,
  "required_scopes": ["prediction:submit", "credits:stake"]
}
```

`requires_stake: true` → 必须携带 `amount`。先用 `ha_credits` 查余额，再提交：

```json
{
  "challenge_id": "a31b…",
  "prediction": {"mean": 0.25, "std": 0.08},
  "amount": 10,
  "idempotency_key": "us-cpi-2026-10-round1"
}
```

返回中带有 `"stake": {"amount": 10.0, "status": "locked"}`。
若请求超时需要重试，**复用同一个** `idempotency_key` 且请求体不变 —— 服务端会重放原始结果，不会重复质押。

## 二元概率（公共事件轮）

```json
{
  "id": "c9e2…",
  "outcome_shape": "binary_probability",
  "prediction_schema": {"type": "binary_probability", "accepted_encodings": ["yes_probability"]}
}
```

```json
{
  "challenge_id": "c9e2…",
  "prediction": {"yes_probability": 0.65},
  "amount": 5
}
```

## 缺少 scope —— 走恢复路径，不要盲目重试

用户未授予 `prediction:submit` 时调用 `ha_predict`，返回的是工具错误信封，绝不会伪装成 schema 错误：

```json
{"error": {"code": "missing_scope", "recoverable": true, "missing_scopes": ["prediction:submit"]}}
```

修复动作是人的动作：请用户重新连接 WorkBuddy，并在授权页勾选缺失的 scope。质押需要 `credits:stake` 而未授予时，返回同样的信封形状。

## 旧版格式会被拒绝

```json
{"challenge_id": "7dc0…", "prediction": {"direction": "bullish", "confidence": 0.7}}
```

→ `invalid_prediction_schema`。请按该挑战的 `prediction_schema` 重新构造（三元挑战用 `probabilities` 概率向量）。
