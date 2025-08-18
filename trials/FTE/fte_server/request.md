### 1. **Request Setup**

- **Method:** `POST`
- **URL:** `http://localhost:8095/ft/decide`

---

### 2. **Headers**

| Key          | Value            |
| ------------ | ---------------- |
| Content-Type | application/json |

---

### Body 1 – Replication likely (high criticality + low reliability)

```json
{
  "task_id": "t200",
  "criticality": 0.98,
  "progress": 0.1,
  "deadline": "2025-08-19T15:00:00Z",
  "resource": {
    "node_id": "n8",
    "reliability": 0.4,
    "spot_eviction_prob": 0.7
  },
  "cost_estimates": {
    "replicate": 1.0,
    "checkpoint": 0.6,
    "migrate": 0.9,
    "resubmit": 0.3
  },
  "sla_penalty": 200.0
}
```

---

### Body 2 – Checkpoint likely (mid criticality + decent reliability + cheap checkpoint)

```json
{
  "task_id": "t201",
  "criticality": 0.65,
  "progress": 0.55,
  "deadline": "2025-08-21T09:00:00Z",
  "resource": {
    "node_id": "n3",
    "reliability": 0.75,
    "spot_eviction_prob": 0.25
  },
  "cost_estimates": {
    "replicate": 2.0,
    "checkpoint": 0.4,
    "migrate": 1.2,
    "resubmit": 0.7
  },
  "sla_penalty": 80.0
}
```

---

### Body 3 – Migration likely (progress high but node unstable)

```json
{
  "task_id": "t202",
  "criticality": 0.8,
  "progress": 0.7,
  "deadline": "2025-08-18T18:00:00Z",
  "resource": {
    "node_id": "n2",
    "reliability": 0.45,
    "spot_eviction_prob": 0.6
  },
  "cost_estimates": {
    "replicate": 1.8,
    "checkpoint": 0.7,
    "migrate": 0.5,
    "resubmit": 1.0
  },
  "sla_penalty": 150.0
}
```

---

### Body 4 – Resubmit likely (low progress, low criticality, low SLA penalty)

```json
{
  "task_id": "t203",
  "criticality": 0.3,
  "progress": 0.05,
  "deadline": "2025-08-25T23:00:00Z",
  "resource": {
    "node_id": "n1",
    "reliability": 0.8,
    "spot_eviction_prob": 0.2
  },
  "cost_estimates": {
    "replicate": 2.5,
    "checkpoint": 1.5,
    "migrate": 1.2,
    "resubmit": 0.3
  },
  "sla_penalty": 20.0
}
```

---

### Body 5 – No action / continue (safe node + low eviction)

```json
{
  "task_id": "t204",
  "criticality": 0.5,
  "progress": 0.6,
  "deadline": "2025-08-22T10:00:00Z",
  "resource": {
    "node_id": "n7",
    "reliability": 0.95,
    "spot_eviction_prob": 0.05
  },
  "cost_estimates": {
    "replicate": 1.0,
    "checkpoint": 0.5,
    "migrate": 0.9,
    "resubmit": 0.2
  },
  "sla_penalty": 50.0
}
```

---

### Body 6 – Mixed case (tight deadline + medium risk → replicate/migrate tradeoff)

```json
{
  "task_id": "t205",
  "criticality": 0.85,
  "progress": 0.4,
  "deadline": "2025-08-18T20:30:00Z",
  "resource": {
    "node_id": "n4",
    "reliability": 0.55,
    "spot_eviction_prob": 0.4
  },
  "cost_estimates": {
    "replicate": 0.9,
    "checkpoint": 0.8,
    "migrate": 0.6,
    "resubmit": 0.5
  },
  "sla_penalty": 120.0
}
```
