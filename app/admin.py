"""Admin endpoints for building the product catalog: products, their questions, and their
rules. This is the "new product/rule = data change, not a code change" path from a UI
instead of hand-writing a SQL seed file - same tables, same constraints, just via HTTP.

Unlike the rest of this API (public, unauthenticated, applicant-facing - see app/quotes.py's
X-Quote-Token model), these endpoints mutate the shared catalog every applicant reads from,
so anyone who could call them could deface the product list. There's exactly one admin (you),
not one caller per resource, so a single static key is enough - no need for the per-row
ownership model the quote endpoints use.
"""

import os

import asyncpg
from fastapi import APIRouter, Header, HTTPException

from app import db
from app.models import (
    ProductCreate,
    ProductOut,
    QuestionCreate,
    QuestionOut,
    RuleConditionIn,
    RuleCreate,
    RuleOut,
)

router = APIRouter()


def _require_admin(x_admin_key: str = Header(..., alias="X-Admin-Key")) -> None:
    # os.environ[...], not .get() with a fallback - a missing ADMIN_API_KEY should fail
    # loudly (500) rather than silently compare against "" and let an empty header through
    if x_admin_key != os.environ["ADMIN_API_KEY"]:
        raise HTTPException(status_code=401, detail="Invalid admin key")


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    body: ProductCreate, x_admin_key: str = Header(..., alias="X-Admin-Key")
) -> ProductOut:
    _require_admin(x_admin_key)
    pool = db.get_pool()

    try:
        row = await pool.fetchrow(
            "INSERT INTO insurance_product (code, name, description) VALUES ($1, $2, $3) "
            "RETURNING code, name, description",
            body.code,
            body.name,
            body.description,
        )
    except asyncpg.exceptions.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail=f"Product code already exists: {body.code}") from exc

    return ProductOut(**dict(row))


@router.post("/products/{code}/questions", response_model=QuestionOut, status_code=201)
async def add_product_question(
    code: str, body: QuestionCreate, x_admin_key: str = Header(..., alias="X-Admin-Key")
) -> QuestionOut:
    _require_admin(x_admin_key)
    pool = db.get_pool()

    product = await pool.fetchrow("SELECT id FROM insurance_product WHERE code = $1", code)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown product code: {code}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            # uw_question is a shared pool across products (see sql/phase1_core_schema.sql) -
            # if question_code already exists, reuse it as-is rather than erroring, so the
            # same question can be linked into more than one product. The provided
            # text/answer_type/enum_options only take effect the first time a code is used.
            question = await conn.fetchrow(
                """
                INSERT INTO uw_question (code, text, answer_type, enum_options)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (code) DO NOTHING
                RETURNING id
                """,
                body.question_code,
                body.text,
                body.answer_type,
                body.enum_options,
            )
            if question is None:
                question = await conn.fetchrow(
                    "SELECT id FROM uw_question WHERE code = $1", body.question_code
                )

            try:
                await conn.execute(
                    """
                    INSERT INTO product_question
                        (product_id, question_id, sequence, is_mandatory, expected_answer)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    product["id"],
                    question["id"],
                    body.sequence,
                    body.is_mandatory,
                    body.expected_answer,
                )
            except asyncpg.exceptions.UniqueViolationError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=f"Question {body.question_code} or sequence {body.sequence} "
                    f"already used on product {code}",
                ) from exc

    return QuestionOut(
        question_code=body.question_code,
        text=body.text,
        answer_type=body.answer_type,
        enum_options=body.enum_options,
        sequence=body.sequence,
        is_mandatory=body.is_mandatory,
    )


@router.post("/products/{code}/rules", response_model=RuleOut, status_code=201)
async def add_product_rule(
    code: str, body: RuleCreate, x_admin_key: str = Header(..., alias="X-Admin-Key")
) -> RuleOut:
    _require_admin(x_admin_key)
    pool = db.get_pool()

    product = await pool.fetchrow("SELECT id FROM insurance_product WHERE code = $1", code)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown product code: {code}")

    # resolve question_code -> question_id up front, same reasoning as
    # app/quotes.py's submit_answers: a bad code should fail clearly, not as an opaque
    # FK violation mid-transaction
    codes = [c.question_code for c in body.conditions]
    rows = await pool.fetch("SELECT id, code FROM uw_question WHERE code = ANY($1::text[])", codes)
    question_id_by_code = {row["code"]: row["id"] for row in rows}
    missing = sorted(set(codes) - question_id_by_code.keys())
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown question_code(s): {missing}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            rule = await conn.fetchrow(
                """
                INSERT INTO uw_rule (product_id, name, priority, outcome, stop_evaluation)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                product["id"],
                body.name,
                body.priority,
                body.outcome,
                body.stop_evaluation,
            )
            for condition in body.conditions:
                await conn.execute(
                    """
                    INSERT INTO uw_rule_condition (rule_id, question_id, operator, value)
                    VALUES ($1, $2, $3, $4)
                    """,
                    rule["id"],
                    question_id_by_code[condition.question_code],
                    condition.operator,
                    condition.value,
                )

    return RuleOut(
        id=rule["id"],
        product_code=code,
        name=body.name,
        priority=body.priority,
        outcome=body.outcome,
        stop_evaluation=body.stop_evaluation,
        conditions=[RuleConditionIn(**c.model_dump()) for c in body.conditions],
    )
