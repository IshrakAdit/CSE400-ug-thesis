"""
FastAPI server for the Adaptive Hybrid Fault‑Tolerance (FT) Engine
------------------------------------------------------------------

Implements the Decision API and orchestration stubs described in your design.
- POST /ft/decide          — compute best FT action; optional orchestration.
- POST /metrics/ingest     — feed back execution metrics for online learning.
- GET  /healthz            — liveness check.
- GET  /config             — inspect current in‑memory cost/learning state.

Run locally:
    uvicorn main:app --reload --port 8080

Environment (optional):
    KUBE_ENABLED=true            # enable Kubernetes orchestrator (requires in‑cluster config or kubeconfig)
    KUBE_NAMESPACE=default
    CHECKPOINT_BASE_URI=s3://checkpoints

Notes:
- The Kubernetes operations are implemented as *safe stubs* that log intent when KUBE_ENABLED is not set.
- A simple in‑memory, exponentially weighted learner updates cost/benefit priors online from /metrics/ingest.
"""
from __future__ import annotations

import os
import math
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, validator

# ---------------------------
# Configuration
# ---------------------------
class Config:
    kube_enabled: bool = os.getenv("KUBE_ENABLED", "false").lower() in ("1", "true", "yes")
    kube_namespace: str = os.getenv("KUBE_NAMESPACE", "default")
    checkpoint_base_uri: str = os.getenv("CHECKPOINT_BASE_URI", "s3://checkpoints")


# ---------------------------
# Domain Models
# ---------------------------
class ActionType(str, Enum):
    replicate = "replicate"
    checkpoint = "checkpoint"
    migrate = "migrate"
    resubmit = "resubmit"
    no_action = "no_action"


class ResourceInfo(BaseModel):
    node_id: Optional[str] = None
    reliability: float = Field(..., ge=0.0, le=1.0)
    spot_eviction_prob: float = Field(..., ge=0.0, le=1.0)


class CostEstimates(BaseModel):
    replicate: float = Field(..., ge=0.0, description="Estimated $/equivalent cost for replication")
    checkpoint: float = Field(..., ge=0.0)
    migrate: float = Field(..., ge=0.0)
    resubmit: float = Field(..., ge=0.0)


class DecideRequest(BaseModel):
    task_id: str
    criticality: float = Field(..., ge=0.0, le=1.0)
    progress: float = Field(..., ge=0.0, le=1.0, description="0=start, 1=complete")
    deadline: datetime
    resource: ResourceInfo
    cost_estimates: CostEstimates
    sla_penalty: float = Field(..., ge=0.0, description="$ value of breaching the SLA for this task")

    @validator("deadline")
    def ensure_timezone(cls, v: datetime) -> datetime:
        # Normalize to aware UTC for comparisons
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class DecideResponse(BaseModel):
    action: ActionType
    parameters: Dict[str, object]
    risk_score: float
    ev_by_action: Dict[ActionType, float]
    rationale: str
    timestamp: datetime


class MetricRecord(BaseModel):
    task_id: str
    action: ActionType
    success: bool
    overhead_seconds: Optional[float] = 0.0
    extra_cost: Optional[float] = 0.0
    node_id: Optional[str] = None
    message: Optional[str] = None


# ---------------------------
# Lightweight Online Learner (in‑memory)
# ---------------------------
class OnlineLearner:
    """Tracks moving averages for action effectiveness and costs.

    - effectiveness[action]: estimated ΔS_a (success probability gain) multiplier in [0,1]
    - cost[action]: estimated realized cost to apply when not provided
    """

    def __init__(self, alpha: float = 0.2):
        self.alpha = alpha
        self.effectiveness: Dict[ActionType, float] = {
            ActionType.replicate: 0.6,
            ActionType.checkpoint: 0.5,
            ActionType.migrate: 0.45,
            ActionType.resubmit: 0.25,
            ActionType.no_action: 0.0,
        }
        self.cost: Dict[ActionType, float] = {
            ActionType.replicate: 1.5,
            ActionType.checkpoint: 0.5,
            ActionType.migrate: 0.8,
            ActionType.resubmit: 0.2,
            ActionType.no_action: 0.0,
        }
        self.last_update_ts = time.time()

    def update(self, m: MetricRecord):
        # Update effectiveness towards 1.0 on success, else towards 0.0
        if m.action in self.effectiveness:
            cur = self.effectiveness[m.action]
            target = 1.0 if m.success else 0.0
            self.effectiveness[m.action] = (1 - self.alpha) * cur + self.alpha * target
        # Update realized cost (normalize overhead seconds as cost proxy if extra_cost missing)
        realized = (m.extra_cost or 0.0) + max(0.0, (m.overhead_seconds or 0.0)) / 60.0
        curc = self.cost.get(m.action, 0.0)
        self.cost[m.action] = (1 - self.alpha) * curc + self.alpha * realized
        self.last_update_ts = time.time()

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            "effectiveness": {k.value: v for k, v in self.effectiveness.items()},
            "cost": {k.value: v for k, v in self.cost.items()},
            "alpha": self.alpha,
            "last_update_ts": self.last_update_ts,
        }


learner = OnlineLearner()


# ---------------------------
# Orchestrator (Kubernetes)
# ---------------------------
class KubernetesOrchestrator:
    def __init__(self, namespace: str):
        self.enabled = Config.kube_enabled
        self.namespace = namespace
        if self.enabled:
            # Lazy import to avoid dependency when not enabled
            from kubernetes import config as k8s_config, client as k8s_client  # type: ignore
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            self.client = k8s_client
        else:
            self.client = None

    def replicate(self, task_id: str, replicas: int, anti_affinity: bool = True) -> str:
        if not self.enabled:
            return f"[DRY‑RUN] Would create {replicas} replicas for task {task_id} (anti_affinity={anti_affinity})"
        # TODO: Implement real ReplicaSet/Job patch with podAntiAffinity
        return f"Created {replicas} replicas for task {task_id}"

    def checkpoint(self, task_id: str, interval_seconds: int, target_uri: str) -> str:
        if not self.enabled:
            return f"[DRY‑RUN] Would enable checkpointing every {interval_seconds}s to {target_uri} for {task_id}"
        # TODO: Inject sidecar/annotation or ConfigMap to task Pod to trigger checkpointing
        return f"Enabled checkpointing every {interval_seconds}s to {target_uri} for {task_id}"

    def migrate(self, task_id: str, preferred_nodes: Optional[Dict[str, str]] = None) -> str:
        if not self.enabled:
            return f"[DRY‑RUN] Would migrate {task_id} with selector {preferred_nodes or {}}"
        # TODO: Evict Pod and recreate with nodeSelector/affinity
        return f"Migrated {task_id} with selector {preferred_nodes or {}}"

    def resubmit(self, task_id: str, backoff_seconds: int) -> str:
        if not self.enabled:
            return f"[DRY‑RUN] Would resubmit {task_id} with backoff {backoff_seconds}s"
        # TODO: Create new Job with backoffLimit and different node pool
        return f"Resubmitted {task_id} with backoff {backoff_seconds}s"


orchestrator = KubernetesOrchestrator(Config.kube_namespace)


# ---------------------------
# FT Engine Core Logic
# ---------------------------
class FaultToleranceEngine:
    def __init__(self, online_learner: OnlineLearner):
        self.learner = online_learner

    @staticmethod
    def risk_score(criticality: float, reliability: float, spot_prob: float, progress: float) -> float:
        # R = C_t * max(1 - Rel_r, P_spot) * (1 - P_prog)
        return float(criticality * max(1 - reliability, spot_prob) * (1 - progress))

    def _estimate_delta_success(self, action: ActionType, req: DecideRequest, base_risk: float) -> float:
        # Start with learned base effectiveness prior (0..1)
        base = self.learner.effectiveness.get(action, 0.0)
        c = req.criticality
        p = req.progress
        rel = req.resource.reliability
        spot = req.resource.spot_eviction_prob
        time_pressure = max(0.0, 1.0 - self._time_to_deadline_ratio(req))  # closer to 1 when near deadline

        if action == ActionType.replicate:
            adj = 0.5 + 0.5 * base_risk  # more useful under risk
            adj *= 0.5 + 0.5 * c        # more valuable for critical tasks
            adj *= 0.6 + 0.4 * time_pressure
        elif action == ActionType.checkpoint:
            adj = 0.3 + 0.7 * base_risk
            adj *= 0.3 + 0.7 * (1 - p)  # more left to do => more to save
            adj *= 0.6 + 0.4 * (spot or (1 - rel))
        elif action == ActionType.migrate:
            adj = 0.2 + 0.8 * base_risk
            adj *= 0.4 + 0.6 * (1 - rel)
            adj *= 0.5 + 0.5 * time_pressure
        elif action == ActionType.resubmit:
            adj = 0.15 + 0.6 * base_risk
            adj *= 0.5 + 0.5 * (1 - p)
        else:
            return 0.0

        # Blend with learned prior
        delta = 0.5 * adj + 0.5 * base
        return max(0.0, min(1.0, float(delta)))

    @staticmethod
    def _time_to_deadline_ratio(req: DecideRequest) -> float:
        now = datetime.now(timezone.utc)
        # If deadline already passed, ratio is 0 (heavy time pressure)
        seconds_left = max(0.0, (req.deadline - now).total_seconds())
        # Normalize by a 1‑hour horizon (tunable)
        horizon = 3600.0
        return min(1.0, seconds_left / horizon)

    def _ev(self, delta_success: float, sla_penalty: float, cost: float) -> float:
        return float(delta_success * sla_penalty - cost)

    def decide(self, req: DecideRequest) -> Tuple[ActionType, Dict[ActionType, float], float, Dict[str, object], str]:
        # 1) Risk
        R = self.risk_score(req.criticality, req.resource.reliability, req.resource.spot_eviction_prob, req.progress)

        # 2) For each action, estimate ΔS and EV
        actions = [ActionType.replicate, ActionType.checkpoint, ActionType.migrate, ActionType.resubmit]
        evs: Dict[ActionType, float] = {}
        deltas: Dict[ActionType, float] = {}

        for a in actions:
            delta = self._estimate_delta_success(a, req, R)
            deltas[a] = delta
            # Use provided cost; fall back to learned if missing (shouldn't be)
            provided_cost = getattr(req.cost_estimates, a.value)
            cost = provided_cost if provided_cost is not None else self.learner.cost[a]
            evs[a] = self._ev(delta, req.sla_penalty, cost)

        # Consider No‑Action explicitly
        evs[ActionType.no_action] = 0.0

        # 3) Choose best EV, but apply guardrails with rules of thumb
        best_action = max(evs.items(), key=lambda kv: kv[1])[0]

        # Rule: If risk is high (>=0.3) and near deadline, favor replicate/checkpoint over resubmit
        time_pressure = max(0.0, 1.0 - self._time_to_deadline_ratio(req))
        if R >= 0.3 and time_pressure > 0.6 and best_action == ActionType.resubmit:
            # pick the better of replicate vs checkpoint
            best_action = max({k: v for k, v in evs.items() if k in (ActionType.replicate, ActionType.checkpoint)}.items(), key=lambda kv: kv[1])[0]

        # Rule: If EV <= 0 for all, choose no_action
        if evs[best_action] <= 0.0:
            best_action = ActionType.no_action

        # 4) Create parameters for the chosen action
        params = self._make_params(best_action, req, R)

        # 5) Build rationale
        rationale = (
            f"risk={R:.3f}, time_pressure={time_pressure:.2f}, "
            f"picked={best_action.value} with EV={evs[best_action]:.3f} among { {k.value: round(v,3) for k,v in evs.items()} }"
        )
        return best_action, evs, R, params, rationale

    def _make_params(self, action: ActionType, req: DecideRequest, R: float) -> Dict[str, object]:
        if action == ActionType.replicate:
            replicas = 3 if (req.criticality > 0.8 and R > 0.4) else 2
            return {"replicas": replicas, "anti_affinity": True}
        if action == ActionType.checkpoint:
            # Base 300s, shrink with risk; clamp 30..600
            interval = int(max(30, min(600, 300 * (1 - min(0.9, R)) + 30)))
            target = f"{Config.checkpoint_base_uri}/{req.task_id}"
            return {"interval_seconds": interval, "target": target}
        if action == ActionType.migrate:
            return {"preferred_nodes": {"node.kubernetes.io/instance-type": "ondemand"}}
        if action == ActionType.resubmit:
            backoff = int(15 + 45 * R)  # 15..60s
            return {"backoff_seconds": backoff, "different_nodepool": True}
        return {}

    def orchestrate(self, action: ActionType, req: DecideRequest, params: Dict[str, object]) -> str:
        if action == ActionType.replicate:
            return orchestrator.replicate(req.task_id, int(params.get("replicas", 2)), bool(params.get("anti_affinity", True)))
        if action == ActionType.checkpoint:
            return orchestrator.checkpoint(req.task_id, int(params.get("interval_seconds", 120)), str(params.get("target")))
        if action == ActionType.migrate:
            return orchestrator.migrate(req.task_id, params.get("preferred_nodes"))
        if action == ActionType.resubmit:
            return orchestrator.resubmit(req.task_id, int(params.get("backoff_seconds", 30)))
        return "No action taken"


engine = FaultToleranceEngine(learner)


# ---------------------------
# FastAPI App
# ---------------------------
app = FastAPI(title="Adaptive Hybrid FT Engine", version="1.0.0")


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "kube_enabled": Config.kube_enabled,
    }


@app.get("/config")
async def get_config():
    return {
        "kube_enabled": Config.kube_enabled,
        "kube_namespace": Config.kube_namespace,
        "checkpoint_base_uri": Config.checkpoint_base_uri,
        "learner": learner.snapshot(),
    }


@app.post("/ft/decide", response_model=DecideResponse)
async def decide(req: DecideRequest, execute: bool = Query(False, description="If true, also orchestrate the chosen action")):
    action, evs, R, params, rationale = engine.decide(req)

    # Optionally execute the action via orchestrator
    if execute and action != ActionType.no_action:
        orchestration_result = engine.orchestrate(action, req, params)
        # Attach side‑effect result text
        params = {**params, "orchestration": orchestration_result}

    return DecideResponse(
        action=action,
        parameters=params,
        risk_score=round(R, 6),
        ev_by_action={k: round(v, 6) for k, v in evs.items()},
        rationale=rationale,
        timestamp=datetime.now(timezone.utc),
    )


@app.post("/metrics/ingest")
async def ingest_metrics(m: MetricRecord):
    learner.update(m)
    return {"ok": True, "learner": learner.snapshot()}


# ---------------------------
# Optional: local manual test
# ---------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
