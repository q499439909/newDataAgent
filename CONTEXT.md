# DataAgent Context

DataAgent is an Agent control plane for multimodal data production, including
text, image, audio, video, and structured dataset records. This glossary fixes
the domain language used when discussing dataset versions, runs, repairs, and
delivery status.

## Language

**DatasetVersion**:
An immutable version of a produced dataset, including its manifest, assets, quality result, and lineage.
_Avoid_: Dataset copy, output folder

**Logical DatasetVersion**:
A DatasetVersion that persists a manifest, lineage, audit references, and verifiable asset references without copying every image into a new directory.
_Avoid_: Script-only version, full copy

**RepairedDatasetVersion**:
A new DatasetVersion produced by combining successful assets from a parent DatasetVersion with successful assets from one or more repair runs.
_Avoid_: RepairDataset, DatasetRepair, RepairMerge

**Deliverable Dataset Export**:
A full materialized image dataset produced from a SUCCEEDED DatasetVersion for external consumption.
_Avoid_: DatasetVersion, logical version

**Control Tool**:
A structured model-callable control-plane capability that lets the model propose or query workflow actions through a typed contract.
_Avoid_: Operator, data processing operator, shell tool

**Planning Tool**:
A structured model-callable tool used to retrieve, inspect, compare, or draft planning inputs without mutating workflow state.
_Avoid_: Operator

**Artifact Tool**:
A deterministic tool that compiles or validates DataAgent artifacts such as PipelineArtifact from structured inputs.
_Avoid_: Recipe writer, arbitrary YAML writer

**PipelineArtifact**:
A durable artifact representation of a pipeline draft or version, suitable for validation, review, reuse, or export.
_Avoid_: Recipe

**User Constraint**:
An atomic, user-confirmed condition that a produced asset or dataset must satisfy, including its comparison semantics and required proof.
_Avoid_: Keyword rule, prompt fragment

**System Invariant**:
A platform-enforced condition that applies independently of the user's requested filtering criteria, such as source immutability.
_Avoid_: User Constraint, inferred user requirement

**Evidence Claim**:
A versioned assertion produced by an Operator about an asset or dataset, including provenance but not an assumption that the assertion is correct.
_Avoid_: Fact, verified result

**Validated Evidence**:
An Evidence Claim produced by a qualified Operator or Pipeline and accepted through the applicable verification path, such as deterministic recomputation, qualified model output, or human review.
_Avoid_: Present field, Operator output

**Requirement Draft**:
The Data Task Planning Agent's implementation-neutral interpretation of a user request. It contains the objective, atomic Constraint Contracts, ambiguities, assumptions, and acceptance intent, but never concrete Operators, models, parameters, or Pipeline order.
_Avoid_: Pipeline proposal, Operator selection

**Requirement Agent**:
The root user-facing Agent that owns the requirement goal, reads WorkOrder facts, delegates specialist planning through LangGraph, and replans from Observations. Requirement planning is one internal phase of this Agent, not a separate Main Agent.
_Avoid_: Requirement parser, Main Agent, Conversation intent router

**Agent Action**:
A structured Requirement Agent decision to respond, delegate through LangGraph, call a Control Tool, ask the user, or finish.
_Avoid_: Conversation keyword, free-form route name

**WorkOrder Liveness**:
The structural classification of a WorkOrder as runnable, waiting for an explicit user decision, or terminal. A runnable WorkOrder must have a required next action.
_Avoid_: Continue keyword, chat intent

**Constraint Contract**:
An atomic, source-traceable statement of one observable target, comparator, value, unit, hardness, and required Evidence type. Constraint identifiers and targets are task-generated and must not be tied to one regression dataset.
_Avoid_: Scenario field, fixed business rule

**Constraint Parameter Binding**:
The Processing Agent's versioned mapping from a Constraint Contract to parameters declared by a selected Operator. Binding follows comparator and parameter-schema semantics; it never supplies observed Evidence values.
_Avoid_: Evidence value, filename expectation

**Golden Task**:
A representative end-to-end acceptance scenario kept outside production planning logic and used to test general Agent behaviour.
_Avoid_: Production template, hard-coded workflow

**Operator**:
A versioned data-processing capability used inside a Pipeline to inspect, transform, filter, or annotate assets.
_Avoid_: Control Tool

**Excluded Asset**:
An asset deliberately left out of a deliverable DatasetVersion after explicit user confirmation, while still being recorded in the delivered manifest and audit evidence.
_Avoid_: Deleted asset, hidden failure

**Abandoned Asset**:
An asset that reached the configured repair attempt limit and is no longer retried automatically.
_Avoid_: Failed asset, successful asset

**Repair Run**:
A run that processes only assets that failed in a previous run or DatasetVersion, with the intent of producing repair inputs for a RepairedDatasetVersion.
_Avoid_: Rerun, full rerun

**Repair Scope**:
The set of failed or missing assets that a Repair Run is allowed to process. Successful assets from the parent DatasetVersion are outside the repair scope.
_Avoid_: Full input, parent dataset

**Operation Lineage**:
The relationship from a Repair Run back to the run whose failed assets it is repairing.
_Avoid_: Dataset lineage

**Delivery Lineage**:
The relationship from a RepairedDatasetVersion back to its parent DatasetVersion and the repair runs that contributed assets to the delivered version.
_Avoid_: Run lineage

**PARTIAL**:
A non-final dataset state meaning usable outputs exist but at least one asset still requires repair before delivery.
_Avoid_: Success, final delivery
