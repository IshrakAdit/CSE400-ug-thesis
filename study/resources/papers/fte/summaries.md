## Paper Summary: FTWS – Fault-Tolerant Workflow Scheduling with Replication & Resubmission

- Goal: **Schedule workflows in the Cloud** within deadlines while **handling task failures**.

- Uses **Replication** and **Resubmission** jointly:

  - Replication → reduces **task completion time** under failures
  - Resubmission → improves **resource efficiency**

- Introduces a **priority-based heuristic** to decide how to apply replication/resubmission:

  - Task priority based on **criticality**, **out-degree**, **earliest deadline**, and **resubmission impact**
  - Heuristic metric balances **replication factor vs. resubmission factor** to reduce resource waste

- **Deadline-oriented scheduling**: ensures tasks meet deadlines even under random failures.

- **Online decision-making**: works **without historical failure data**.

## Paper Summary: ICFWS – Imbalance-based Fault-Tolerant Workflow Scheduling

- Consdiers **Resubmission** and **replication** for -

  - Resubmission → better **resource utilization**
  - Replication → shorter **task completion time under failures**

- Most existing research considers these techniques **independently**, not jointly—especially in **Cloud workflow scheduling**.
- The paper proposes **ICFWS (Imbalance-based Cloud Fault-Tolerant Workflow Scheduling)**, a novel algorithm that **combines resubmission and replication**.
- Key ideas of ICFWS:

  - Divides the workflow’s **soft deadline into sub-deadlines** for individual tasks.
  - Uses **imbalance characteristics among task sub-deadlines** to decide:

    - Which fault-tolerance strategy to apply (resubmission vs. replication)
    - How much resource to reserve for each task

  - Leverages **on-demand resource provisioning** in Cloud systems.

- An **online scheduling and dynamic adjustment scheme** is introduced to:

  - Reschedule tasks using resubmission when failures occur
  - Adjust sub-deadlines and fault-tolerance strategies of unexecuted tasks at runtime

## Paper Summary: ReadyFS Fault-Tolerant Scheduling

- Journal: ScienceDirect

- Identifies **three major resource failure types**:

  - **Host Permanent Failure (HPF)**
  - **Host Transient Failure (HTF)** (often neglected in prior works)
  - **VM Transient Failure (VMTF)**

- Existing fault-tolerant (FT) approaches usually handle **only one or two failure types**, leading to reduced reliability.
- The paper proposes **ReadyFS**, a **real-time and dynamic fault-tolerant scheduling algorithm** for cloud-based scientific workflows.
- ReadyFS introduces **three FT mechanisms**:

  - **Replication with Delay Execution (RDE)** → handles HPF and VMTF
  - **Checkpointing with Delay Execution (CDE)** → handles HPF and VMTF
  - **Rescheduling (ReSC)** → handles HTF affecting datacenter-wide availability

- A **Resource Adjustment (RA)** strategy is added to improve utilization:

  - **Resource Scaling-Up (RS-Up)**
  - **Resource Scaling-Down (RS-Down)**

- ReadyFS **combines FT mechanisms with dynamic resource adjustment**
