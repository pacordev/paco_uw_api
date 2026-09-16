"""Pydantic response shapes for the read endpoints - gives FastAPI its request
validation, response filtering, and the /docs page for free.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

Strategy = Literal["full", "short_circuit"]
Outcome = Literal["accept", "increase_premium", "refer_to_insurer", "decline"]
# matches uw_question.answer_type's implicit contract (see sql/phase5_hardening.sql's
# quote_answer_validate trigger) and uw_rule_condition.operator (see
# sql/phase3_rules_engine.sql) - neither is a DB-level CHECK constraint, so this Literal
# is the only thing stopping a typo'd value from reaching either table.
AnswerType = Literal["boolean", "number", "text", "enum"]
Operator = Literal["=", "<>", ">", ">=", "<", "<="]


class ErrorOut(BaseModel):
    # every error response on this API looks like this - registered in app/main.py as the
    # documented 422 shape so /docs stops advertising FastAPI's default HTTPValidationError
    # (a list under detail), which app/main.py's RequestValidationError handler no longer sends
    detail: str


class ProductOut(BaseModel):
    code: str
    name: str
    description: str | None = None


class QuestionOut(BaseModel):
    question_code: str
    text: str
    answer_type: str
    enum_options: list[str] | None = None
    sequence: int
    is_mandatory: bool


class QuoteCreate(BaseModel):
    product_code: str


class QuoteOut(BaseModel):
    quote_id: int
    access_token: str


class AnswerIn(BaseModel):
    question_code: str
    answer_text: str


class AnswersIn(BaseModel):
    answers: list[AnswerIn]


class AnswerOut(BaseModel):
    question_code: str
    answer_text: str


class AnswersSubmitOut(BaseModel):
    quote_id: int
    answers: list[AnswerOut]


class EvaluateIn(BaseModel):
    strategy: Strategy


class EvaluateOut(BaseModel):
    quote_id: int
    strategy: Strategy
    outcome: Outcome
    evaluated_at: datetime


class ProductCreate(BaseModel):
    code: str
    name: str
    description: str | None = None


class QuestionCreate(BaseModel):
    question_code: str
    text: str
    answer_type: AnswerType
    enum_options: list[str] | None = None
    sequence: int
    is_mandatory: bool = True
    expected_answer: str | None = None

    @model_validator(mode="after")
    def _enum_needs_options(self) -> "QuestionCreate":
        if self.answer_type == "enum" and not self.enum_options:
            raise ValueError("enum_options is required when answer_type is 'enum'")
        return self


class RuleConditionIn(BaseModel):
    question_code: str
    operator: Operator
    value: str


class RuleCreate(BaseModel):
    name: str
    priority: int = 1
    outcome: Outcome
    stop_evaluation: bool = False
    conditions: list[RuleConditionIn]

    @model_validator(mode="after")
    def _needs_a_condition(self) -> "RuleCreate":
        if not self.conditions:
            raise ValueError("a rule needs at least one condition")
        return self


class RuleOut(BaseModel):
    id: int
    product_code: str
    name: str
    priority: int
    outcome: Outcome
    stop_evaluation: bool
    conditions: list[RuleConditionIn]
