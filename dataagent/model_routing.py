from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ModelTaskKind(StrEnum):
    CONVERSATION = "conversation"
    INTENT_CLASSIFICATION = "intent_classification"
    REQUIREMENT_PLANNING = "requirement_planning"
    RETRIEVAL_PLANNING = "retrieval_planning"
    PROCESSING_PLANNING = "processing_planning"
    STRATEGY_PLANNING = "strategy_planning"
    CODE_GENERATION = "code_generation"
    VISION_EVALUATION = "vision_evaluation"
    IMAGE_GENERATION = "image_generation"
    IMAGE_GENERATION_PRO = "image_generation_pro"
    TEXT_IMAGE_GENERATION = "text_image_generation"


class ThinkingEffort(StrEnum):
    DISABLED = "disabled"
    MEDIUM = "medium"
    HIGH = "high"


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_kind: ModelTaskKind
    capability: str
    model_id: str
    thinking_effort: ThinkingEffort = ThinkingEffort.DISABLED
    fallback_chain: tuple[str, ...] = ()
    reason: str


class ModelRoutingPolicy:
    """Routes typed capabilities; Agent names are deliberately not routing keys."""

    def __init__(
        self,
        *,
        fast_text_model: str,
        reasoning_model: str,
        vision_model: str,
        image_generation_model: str,
        image_generation_pro_model: str,
        text_image_model: str,
    ) -> None:
        self.fast_text_model = fast_text_model
        self.reasoning_model = reasoning_model
        self.vision_model = vision_model
        self.image_generation_model = image_generation_model
        self.image_generation_pro_model = image_generation_pro_model
        self.text_image_model = text_image_model

    @classmethod
    def from_settings(cls, settings) -> "ModelRoutingPolicy":
        return cls(
            fast_text_model=settings.fast_text_model,
            reasoning_model=settings.planning_model,
            vision_model=settings.vision_model,
            image_generation_model=settings.image_generation_model,
            image_generation_pro_model=settings.image_generation_pro_model,
            text_image_model=settings.text_image_model,
        )

    def route(self, task_kind: ModelTaskKind) -> ModelRoute:
        if task_kind in {
            ModelTaskKind.CONVERSATION,
            ModelTaskKind.INTENT_CLASSIFICATION,
            ModelTaskKind.RETRIEVAL_PLANNING,
        }:
            return ModelRoute(
                task_kind=task_kind,
                capability="fast_text",
                model_id=self.fast_text_model,
                fallback_chain=(self.reasoning_model,),
                reason="Low-latency text task with reasoning escalation available.",
            )
        if task_kind in {
            ModelTaskKind.REQUIREMENT_PLANNING,
            ModelTaskKind.PROCESSING_PLANNING,
            ModelTaskKind.STRATEGY_PLANNING,
            ModelTaskKind.CODE_GENERATION,
        }:
            return ModelRoute(
                task_kind=task_kind,
                capability="reasoning",
                model_id=self.reasoning_model,
                reason="Long-horizon planning, reasoning, or code task.",
            )
        if task_kind == ModelTaskKind.VISION_EVALUATION:
            return ModelRoute(
                task_kind=task_kind,
                capability="vision",
                model_id=self.vision_model,
                reason="The request contains image evidence.",
            )
        image_models = {
            ModelTaskKind.IMAGE_GENERATION: self.image_generation_model,
            ModelTaskKind.IMAGE_GENERATION_PRO: self.image_generation_pro_model,
            ModelTaskKind.TEXT_IMAGE_GENERATION: self.text_image_model,
        }
        try:
            model_id = image_models[task_kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported model task kind: {task_kind}") from exc
        return ModelRoute(
            task_kind=task_kind,
            capability="image_generation",
            model_id=model_id,
            reason="Image generation is isolated behind an Operator route.",
        )


__all__ = [
    "ModelRoute",
    "ModelRoutingPolicy",
    "ModelTaskKind",
    "ThinkingEffort",
]
