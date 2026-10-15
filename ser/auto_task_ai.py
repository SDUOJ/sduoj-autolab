"""Public contracts for the AI extension of /auto-task (Pydantic 1.x)."""

from typing import Any, Dict, List, Optional
from typing_extensions import Literal

from pydantic import BaseModel, Field, validator


TaskType = Literal["question.extract", "question.solve", "question.verify", "question.chat",
                   "authoring.generate", "subjective.grade", "plagiarism.scan", "ide.execute",
                   "ide.tutor", "course.index", "course.ask", "interview.advance", "exam.plan"]
ResourceKind = Literal["question", "collection", "authoring", "grading", "plagiarism", "ide",
                       "course", "document", "conversation", "interview", "exam"]


class ResourceCreate(BaseModel):
    kind: ResourceKind
    data: Dict[str, Any] = Field(default_factory=dict)
    course_id: Optional[int] = None
    psid: Optional[int] = None
    parent_id: Optional[str] = None


class ResourceUpdate(BaseModel):
    version: int = Field(..., ge=1)
    data: Dict[str, Any]


class TaskCreate(BaseModel):
    task_type: TaskType
    operation_key: str = Field(..., min_length=1, max_length=128)
    resource_id: str = Field(..., min_length=1, max_length=63)
    payload: Dict[str, Any] = Field(default_factory=dict)


class PageRequest(BaseModel):
    psid: Optional[int] = Field(None, ge=1)
    pageNow: int = Field(1, ge=1)
    pageSize: int = Field(20, ge=1, le=100)
    kind: Optional[ResourceKind] = None
    keyword: str = Field("", max_length=200)
    status: Optional[str] = None
    course_id: Optional[int] = None
    tags: List[str] = Field(default_factory=list)


class Option(BaseModel):
    label: str
    original: str
    zh: str


class Figure(BaseModel):
    source_image: int = Field(..., ge=0)
    caption: str
    bbox: List[float] = Field(..., description="Normalized left, top, right, bottom coordinates")
    svg: str = ""

    @validator("bbox")
    def valid_bbox(cls, value):
        if len(value) != 4 or not all(0 <= n <= 1 for n in value) or value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("Invalid normalized bounding box")
        return value


class Question(BaseModel):
    question_type: Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer", "calculation", "proof", "programming"]
    language: str
    original: str = Field(..., description="Original-language question stem only, without option labels/text")
    zh: str = Field(..., description="Chinese question stem only; options are separate")
    options: List[Option] = Field(default_factory=list)
    answer: str = ""
    explanation: str = ""
    tags: List[str] = Field(default_factory=list)
    figures: List[Figure] = Field(default_factory=list)


class Extraction(BaseModel):
    questions: List[Question]


class DuplicateDecision(BaseModel):
    same: bool
    matched_id: str
    reason: str


class Answer(BaseModel):
    answer: str
    explanation: str
    tags: List[str]


class ExecutionCase(BaseModel):
    input: str
    output: str


class Verification(BaseModel):
    passed: bool
    evidence: List[str]
    corrected_answer: str
    explanation: str
    requires_execution: bool = False
    verification_code: str = ""
    language: str = "python"
    tests: List[ExecutionCase] = Field(default_factory=list)


class Feedback(BaseModel):
    answer: str
    citations: List[str] = Field(default_factory=list)


class Criterion(BaseModel):
    name: str
    score: float = Field(..., ge=0)
    max_score: float = Field(..., ge=0)
    evidence: str

    @validator("max_score")
    def bounded_score(cls, value, values):
        if values.get("score", 0) > value:
            raise ValueError("Score exceeds rubric maximum")
        return value


class Grade(BaseModel):
    criteria: List[Criterion]
    confidence: float = Field(..., ge=0, le=1)
    summary: str
    needs_review: bool


class Moderation(BaseModel):
    decision: Literal["normal", "warning", "violation"]
    reason: str


class InterviewTurn(BaseModel):
    question: str
    focus: str
    complete: bool
    summary: str
    score: float = Field(..., ge=0, le=100)
    confidence: float = Field(..., ge=0, le=1)
    criteria: List[Criterion] = Field(default_factory=list)
    previous_answer_evaluation: str = ""
    source_type: Literal["question", "code", "report", "none"] = "none"
    source_start_line: int = Field(0, ge=0)
    source_end_line: int = Field(0, ge=0)
    integrity_conclusion: Literal["pass", "review", "fail"] = "review"


class Authoring(BaseModel):
    title: str
    description: str
    reference_code: str
    language: Literal["c", "cpp", "python", "java"]
    tests: List[ExecutionCase]
    tags: List[str]
    explanation: str


class ReviewUpdate(BaseModel):
    decision: Literal["normal", "warning", "violation"]
    reason: str = Field(..., min_length=1, max_length=2000)


class PointAdjustment(BaseModel):
    subject_id: str = Field(..., min_length=1, max_length=64)
    amount: float
    operation_key: str = Field(..., min_length=1, max_length=96)
    reason: str = Field(..., min_length=1, max_length=2000)

    @validator("amount")
    def finite_amount(cls, value):
        import math
        if not math.isfinite(value) or abs(value) > 1000000000:
            raise ValueError("Invalid points adjustment")
        return value


def output_schema(model):
    schema = model.schema()
    def close(value):
        if isinstance(value, dict):
            if value.get("type") == "object" and "properties" in value:
                value["additionalProperties"] = False
                value["required"] = list(value["properties"])
            value.pop("default", None)
            for child in value.values():
                close(child)
        elif isinstance(value, list):
            for child in value:
                close(child)
    close(schema)
    return schema
