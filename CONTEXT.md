# DataAgent Context

DataAgent is an Agent control plane for image data production. This glossary fixes the domain language used when discussing dataset versions, runs, repairs, and delivery status.

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
