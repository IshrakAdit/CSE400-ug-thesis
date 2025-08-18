### 1. **Request Setup**

- **Method:** `POST`
- **URL:** `http://localhost:8095/ft/decide`

---

### 2. **Headers**

| Key          | Value            |
| ------------ | ---------------- |
| Content-Type | application/json |

---

### 3. **Body (raw, JSON)**

```json
{
  "task_id": "t123",
  "criticality": 0.92,
  "progress": 0.35,
  "deadline": "2025-08-20T12:00:00Z",
  "resource": {
    "node_id": "n5",
    "reliability": 0.68,
    "spot_eviction_prob": 0.45
  },
  "cost_estimates": {
    "replicate": 1.5,
    "checkpoint": 0.5,
    "migrate": 0.8,
    "resubmit": 0.2
  },
  "sla_penalty": 100.0
}
```
