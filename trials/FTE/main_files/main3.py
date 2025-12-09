"""
Adaptive Hybrid FT Engine - full rewritten main.py

Run:
    uvicorn adaptive_ft_engine_main:app --reload --port 8080

Endpoints:
    GET  /healthz
    GET  /config
    POST /ft/decide?execute=false
    POST /metrics/ingest

Notes:
- Kubernetes orchestrator remains a dry-run stub unless KUBE_ENABLED=true.
- Core decision: EV = (ΔS * SLA_penalty * remaining_factor) - cost
  where remaining_factor = (1 - progress) ** GAMMA

Tunables (class FaultToleranceEngine):
- GAMMA: importance of remaining work
- REPLICA_COST_FACTOR: multiplies replicate cost to account for infra overhead
- MIN_EV_TO_ACT: minimum EV ($) to perform any action

"""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional, Tuple, Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field, validator

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(level=os.getenv("FT_LOG_LEVEL", "INFO"))
logger = logging.getLogger("ft-engine")

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
    replicate: float = Field(..., ge=0.0)
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
    parameters: Dict[str, Any]
    risk_score: float
    ev_by_action: Dict[str, float]
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
# Lightweight Online Learner
# ---------------------------
class OnlineLearner:
    """
    Tracks EWMA priors for action effectiveness and cost.
    effectiveness[action] \in [0,1]
    cost[action] = expected $ cost
    """
    def __init__(self, alpha: float = 0.2):
        self.alpha = float(alpha)
        # Conservative priors
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
        a = m.action
        # Update effectiveness
        if a in self.effectiveness:
            cur_eff = self.effectiveness[a]
            target = 1.0 if m.success else 0.0
            new_eff = (1 - self.alpha) * cur_eff + self.alpha * target
            self.effectiveness[a] = max(0.0, min(1.0, new_eff))
            logger.debug("Learner effectiveness[%s]: %.4f -> %.4f (success=%s)", a.value, cur_eff, new_eff, m.success)

        # Update cost: normalize overhead seconds (minutes) + extra_cost
        realized = float(m.extra_cost or 0.0) + max(0.0, float(m.overhead_seconds or 0.0)) / 60.0
        cur_cost = self.cost.get(a, 0.0)
        new_cost = (1 - self.alpha) * cur_cost + self.alpha * realized
        self.cost[a] = max(0.0, new_cost)
        self.last_update_ts = time.time()
        logger.debug("Learner cost[%s]: %.4f -> %.4f (realized=%.4f)", a.value, cur_cost, new_cost, realized)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "effectiveness": {k.value: float(v) for k, v in self.effectiveness.items()},
            "cost": {k.value: float(v) for k, v in self.cost.items()},
            "alpha": float(self.alpha),
            "last_update_ts": float(self.last_update_ts),
        }


learner = OnlineLearner()


# ---------------------------
# Orchestrator (Kubernetes) - safe stubs
# ---------------------------
class KubernetesOrchestrator:
    def __init__(self, namespace: str):
        self.enabled = Config.kube_enabled
        self.namespace = namespace
        if self.enabled:
            try:
                from kubernetes import config as k8s_config, client as k8s_client  # type: ignore
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                self.client = k8s_client
                logger.info("Kubernetes orchestrator enabled in namespace %s", namespace)
            except Exception as exc:
                logger.exception("Failed to initialize Kubernetes client, falling back to dry-run: %s", exc)
                self.enabled = False
                self.client = None
        else:
            self.client = None

    def replicate(self, task_id: str, replicas: int, anti_affinity: bool = True) -> str:
        if not self.enabled:
            s = f"[DRY-RUN] replicate {task_id} -> replicas={replicas} anti_affinity={anti_affinity}"
            logger.info(s)
            return s
        return f"Created {replicas} replicas for {task_id}"

    def checkpoint(self, task_id: str, interval_seconds: int, target_uri: str) -> str:
        if not self.enabled:
            s = f"[DRY-RUN] checkpoint {task_id} -> every {interval_seconds}s to {target_uri}"
            logger.info(s)
            return s
        return f"Enabled checkpointing for {task_id}"

    def migrate(self, task_id: str, preferred_nodes: Optional[Dict[str, str]] = None) -> str:
        if not self.enabled:
            s = f"[DRY-RUN] migrate {task_id} -> selector={preferred_nodes or {}}"
            logger.info(s)
            return s
        return f"Migrated {task_id}"

    def resubmit(self, task_id: str, backoff_seconds: int) -> str:
        if not self.enabled:
            s = f"[DRY-RUN] resubmit {task_id} -> backoff={backoff_seconds}s"
            logger.info(s)
            return s
        return f"Resubmitted {task_id}"


orchestrator = KubernetesOrchestrator(Config.kube_namespace)


# ---------------------------
# Core FT Engine
# ---------------------------
class FaultToleranceEngine:
    def __init__(self, learner: OnlineLearner):
        self.learner = learner
        # Tunables
        self.GAMMA = float(os.getenv("FT_GAMMA", "2.0"))
        self.REPLICA_COST_FACTOR = float(os.getenv("FT_REPLICA_COST_FACTOR", "3.0"))
        self.MIN_EV_TO_ACT = float(os.getenv("FT_MIN_EV_TO_ACT", "0.5"))

        # stable action preference for deterministic tie-breaking
        self._action_priority = [
            ActionType.replicate,
            ActionType.checkpoint,
            ActionType.migrate,
            ActionType.resubmit,
            ActionType.no_action,
        ]

    @staticmethod
    def risk_score(criticality: float, reliability: float, spot_prob: float, progress: float) -> float:
        """
        R = criticality * max(1 - reliability, spot_prob) * (1 - progress)
        bounded to [0,1]
        """
        basic = criticality * max(1.0 - reliability, spot_prob) * (1.0 - progress)
        return float(max(0.0, min(1.0, basic)))

    def _time_to_deadline_ratio(self, req: DecideRequest, horizon_seconds: float = 3600.0) -> float:
        now = datetime.now(timezone.utc)
        seconds_left = max(0.0, (req.deadline - now).total_seconds())
        return float(min(1.0, seconds_left / float(horizon_seconds)))

    def _estimate_delta_success(self, action: ActionType, req: DecideRequest, base_risk: float) -> float:
        base_prior = float(self.learner.effectiveness.get(action, 0.0))
        time_pressure = 1.0 - self._time_to_deadline_ratio(req)
        c = float(req.criticality)
        p = float(req.progress)
        rel = float(req.resource.reliability)
        spot = float(req.resource.spot_eviction_prob)

        if action == ActionType.replicate:
            adj = 0.25 + 0.75 * base_risk
            adj *= (0.4 + 0.6 * c)
            adj *= (0.5 + 0.5 * time_pressure)
        elif action == ActionType.checkpoint:
            adj = 0.20 + 0.80 * base_risk
            adj *= (0.4 + 0.6 * (1.0 - p))
            adj *= (0.5 + 0.5 * max(spot, (1.0 - rel)))
        elif action == ActionType.migrate:
            adj = 0.15 + 0.85 * base_risk
            adj *= (0.5 + 0.5 * (1.0 - rel))
            adj *= (0.4 + 0.6 * time_pressure)
        elif action == ActionType.resubmit:
            adj = 0.10 + 0.65 * base_risk
            adj *= (0.5 + 0.5 * (1.0 - p))
            adj *= (0.4 + 0.6 * (1.0 - time_pressure))
        else:
            return 0.0

        delta = 0.4 * base_prior + 0.6 * adj
        delta = float(max(0.0, min(1.0, delta)))
        logger.debug("ΔS estimate for %s -> base_prior=%.4f adj=%.4f delta=%.4f", action.value, base_prior, adj, delta)
        return delta

    def _ev(self, delta_success: float, sla_penalty: float, cost: float) -> float:
        ev = float(delta_success * sla_penalty - cost)
        return ev

    def _select_best_action(
        self,
        evs: Dict[ActionType, float],
        deltas: Dict[ActionType, float],
        costs: Dict[ActionType, float],
        risk: float,
        time_pressure: float,
    ) -> ActionType:
        # guardrail: deprioritize resubmit under high risk & time pressure
        if risk >= 0.3 and time_pressure > 0.6 and ActionType.resubmit in evs:
            evs = evs.copy()
            evs[ActionType.resubmit] = evs.get(ActionType.resubmit, 0.0) - 1e-6

        max_ev = max(evs.values())
        if max_ev <= 0.0:
            return ActionType.no_action

        eps = 1e-9
        candidates = [a for a, v in evs.items() if abs(v - max_ev) <= eps]
        if len(candidates) == 1:
            return candidates[0]

        candidates.sort(
            key=lambda a: (
                -float(deltas.get(a, 0.0)),
                float(costs.get(a, float("inf"))),
                self._action_priority.index(a),
            )
        )
        return candidates[0]

    def _make_params(self, action: ActionType, req: DecideRequest, R: float) -> Dict[str, Any]:
        if action == ActionType.replicate:
            replicas = 3 if (req.criticality > 0.8 and R > 0.4) else 2
            return {"replicas": int(replicas), "anti_affinity": True}
        if action == ActionType.checkpoint:
            interval = int(max(30, min(600, int(300 * (1.0 - min(0.9, R))) + 30)))
            target = f"{Config.checkpoint_base_uri.rstrip('/')}/{req.task_id}"
            return {"interval_seconds": interval, "target_uri": target}
        if action == ActionType.migrate:
            return {"preferred_nodes": {"node.kubernetes.io/instance-type": "ondemand"}}
        if action == ActionType.resubmit:
            backoff = int(15 + 45 * R)
            return {"backoff_seconds": backoff, "different_nodepool": True}
        return {}

    def orchestrate(self, action: ActionType, req: DecideRequest, params: Dict[str, Any]) -> str:
        if action == ActionType.replicate:
            return orchestrator.replicate(req.task_id, int(params.get("replicas", 2)), bool(params.get("anti_affinity", True)))
        if action == ActionType.checkpoint:
            return orchestrator.checkpoint(req.task_id, int(params.get("interval_seconds", 120)), str(params.get("target_uri", "")))
        if action == ActionType.migrate:
            return orchestrator.migrate(req.task_id, params.get("preferred_nodes"))
        if action == ActionType.resubmit:
            return orchestrator.resubmit(req.task_id, int(params.get("backoff_seconds", 30)))
        return "No action taken"

    def decide(self, req: DecideRequest) -> Tuple[ActionType, Dict[str, float], float, Dict[str, Any], str]:
        # 1) compute risk
        R = self.risk_score(req.criticality, req.resource.reliability, req.resource.spot_eviction_prob, req.progress)
        time_pressure = 1.0 - self._time_to_deadline_ratio(req)

        # 2) compute delta and EV for each actionable type
        actions = [ActionType.replicate, ActionType.checkpoint, ActionType.migrate, ActionType.resubmit]
        evs: Dict[ActionType, float] = {}
        deltas: Dict[ActionType, float] = {}
        costs: Dict[ActionType, float] = {}

        remaining_factor = (1.0 - float(req.progress)) ** float(self.GAMMA)

        for a in actions:
            delta = self._estimate_delta_success(a, req, R)
            deltas[a] = delta

            provided_cost = max(0.0, float(getattr(req.cost_estimates, a.value)))
            cost = provided_cost if provided_cost > 0.0 else float(self.learner.cost.get(a, 0.0))

            if a == ActionType.replicate:
                cost = float(cost) * float(self.REPLICA_COST_FACTOR)

            costs[a] = float(cost)

            benefit = float(delta) * float(req.sla_penalty) * remaining_factor
            evs[a] = float(benefit - cost)

        evs[ActionType.no_action] = 0.0
        deltas[ActionType.no_action] = 0.0
        costs[ActionType.no_action] = 0.0

        # If best EV is below threshold, do nothing
        best_ev_value = max(evs.values())
        if best_ev_value <= float(self.MIN_EV_TO_ACT):
            chosen = ActionType.no_action
        else:
            chosen = self._select_best_action(evs, deltas, costs, R, time_pressure)

        params = self._make_params(chosen, req, R)

        rationale = (
            f"risk={R:.4f}, time_pressure={time_pressure:.3f}, chosen={chosen.value}, "
            + "evs={" + ", ".join(f"{k.value}:{evs[k]:.3f}" for k in evs) + "}"
        )

        logger.debug("DecideRequest task=%s risk=%.4f time_pressure=%.3f", req.task_id, R, time_pressure)
        logger.debug("Deltas: %s", {k.value: round(v, 4) for k, v in deltas.items()})
        logger.debug("Costs: %s", {k.value: round(v, 4) for k, v in costs.items()})
        logger.debug("EVs: %s", {k.value: round(v, 4) for k, v in evs.items()})
        logger.info("Decision for %s -> %s", req.task_id, chosen.value)

        evs_str = {k.value: float(round(v, 6)) for k, v in evs.items()}
        return chosen, evs_str, float(R), params, rationale


engine = FaultToleranceEngine(learner)


# ---------------------------
# FastAPI App
# ---------------------------
app = FastAPI(title="Adaptive Hybrid FT Engine", version="1.2.0")


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
    chosen, evs_str, R, params, rationale = engine.decide(req)

    if execute and chosen != ActionType.no_action:
        orchestration_result = engine.orchestrate(chosen, req, params)
        params = {**params, "orchestration_result": orchestration_result}

    return DecideResponse(
        action=chosen,
        parameters=params,
        risk_score=round(float(R), 6),
        ev_by_action=evs_str,
        rationale=rationale,
        timestamp=datetime.now(timezone.utc),
    )


@app.post("/metrics/ingest")
async def ingest_metrics(m: MetricRecord):
    if m.action not in learner.effectiveness:
        return {"ok": False, "error": f"unknown action {m.action}"}
    learner.update(m)
    return {"ok": True, "learner": learner.snapshot()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("adaptive_ft_engine_main:app", host="0.0.0.0", port=8080, reload=True)
