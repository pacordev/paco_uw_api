"""Quote creation, answer submission, and evaluation.

Answers are upserted (ON CONFLICT DO UPDATE) rather than insert-only, so a client can
resubmit/correct an answer for the same question without a separate PATCH endpoint - the
Phase 5 validation trigger runs BEFORE INSERT OR UPDATE either way, so both paths still get
validated.

quote_id is a bare sequential integer, so every request scoped to an existing quote also
requires the X-Quote-Token header handed back once by POST /quotes (Phase 7's
quote.access_token column) - otherwise anyone could guess/increment a quote_id and read or
overwrite someone else's answers. A wrong token and a nonexistent quote_id get the exact same
404 message, so a caller can't use the response to tell "doesn't exist" apart from "exists,
wrong token".

Deliberately does *not* check that a question_code belongs to the quote's product - the DB
doesn't enforce that either (quote_answer only FKs to uw_question, not product_question), and
adding that check here would mean inventing a business rule at this layer instead of in SQL,
which goes against the whole point of keeping this API thin.

evaluate_quote is a thin wrapper over evaluate_and_record_quote(): the caller always picks
'full' or 'short_circuit' explicitly, no auto-evaluate on the last answer. Both models stay
reachable this way, and there's no "did they answer everything" check here either - same
reasoning as above, that's for the SQL functions to decide, not this layer.

All three POST routes below carry a per-IP @limiter.limit(...) (see app/limiter.py) tighter
than the app-wide default - this is a public, unauthenticated, applicant-facing API, so these
are the endpoints someone could script to spam quotes/answers/evaluations. Each also takes a
`response: Response` param that's never touched directly - slowapi's rate-limit-headers
injection needs a real Response object to attach X-RateLimit-*/Retry-After to, and since these
endpoints return Pydantic models (not a Response) for FastAPI's response_model to serialize,
this is the object it falls back to. FastAPI applies whatever ends up on it to the real
response automatically.
"""

import uuid

import asyncpg
from fastapi import APIRouter, Header, HTTPException, Request, Response

from app import db
from app.limiter import limiter
from app.models import (
    AnswerOut,
    AnswersIn,
    AnswersSubmitOut,
    EvaluateIn,
    EvaluateOut,
    QuoteCreate,
    QuoteOut,
    TriggerOut,
)

router = APIRouter()


async def _require_quote_ownership(pool: asyncpg.Pool, quote_id: int, x_quote_token: str) -> None:
    # same 404 whether the token is malformed, wrong, or the quote_id doesn't exist at all -
    # never confirm a quote_id is real to a caller who doesn't already hold its token
    not_found = HTTPException(status_code=404, detail=f"Unknown quote_id: {quote_id}")
    try:
        token = uuid.UUID(x_quote_token)
    except ValueError:
        raise not_found

    quote = await pool.fetchrow(
        "SELECT id FROM quote WHERE id = $1 AND access_token = $2", quote_id, token
    )
    if quote is None:
        raise not_found


# stricter than the app-wide default (see app/limiter.py) - this is the resource-creation
# endpoint, the one scripted abuse would hit hardest. Arbitrary starting point, tune once
# there's real traffic to look at.
@router.post("/quotes", response_model=QuoteOut, status_code=201)
@limiter.limit("10/minute")
async def create_quote(request: Request, response: Response, body: QuoteCreate) -> QuoteOut:
    pool = db.get_pool()

    product = await pool.fetchrow("SELECT id FROM insurance_product WHERE code = $1", body.product_code)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown product code: {body.product_code}")

    row = await pool.fetchrow(
        "INSERT INTO quote (product_id) VALUES ($1) RETURNING id, access_token", product["id"]
    )
    return QuoteOut(quote_id=row["id"], access_token=str(row["access_token"]))


@router.post("/quotes/{quote_id}/answers", response_model=AnswersSubmitOut)
@limiter.limit("30/minute")
async def submit_answers(
    request: Request,
    response: Response,
    quote_id: int,
    body: AnswersIn,
    x_quote_token: str = Header(..., alias="X-Quote-Token"),
) -> AnswersSubmitOut:
    pool = db.get_pool()
    await _require_quote_ownership(pool, quote_id, x_quote_token)

    if not body.answers:
        raise HTTPException(status_code=400, detail="answers must not be empty")

    # resolve question_code -> question_id up front so a bad code fails clearly, instead
    # of surfacing as an opaque FK violation mid-transaction
    codes = [a.question_code for a in body.answers]
    rows = await pool.fetch("SELECT id, code FROM uw_question WHERE code = ANY($1::text[])", codes)
    question_id_by_code = {row["code"]: row["id"] for row in rows}

    missing = sorted(set(codes) - question_id_by_code.keys())
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown question_code(s): {missing}")

    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                for answer in body.answers:
                    await conn.execute(
                        """
                        INSERT INTO quote_answer (quote_id, question_id, answer_text)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (quote_id, question_id)
                        DO UPDATE SET answer_text = EXCLUDED.answer_text
                        """,
                        quote_id,
                        question_id_by_code[answer.question_code],
                        answer.answer_text,
                    )
            except asyncpg.exceptions.RaiseError as exc:
                # the Phase 5 validation trigger rejecting answer_text for its answer_type
                raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AnswersSubmitOut(
        quote_id=quote_id,
        answers=[AnswerOut(question_code=a.question_code, answer_text=a.answer_text) for a in body.answers],
    )


@router.post("/quotes/{quote_id}/evaluate", response_model=EvaluateOut)
@limiter.limit("20/minute")
async def evaluate_quote(
    request: Request,
    response: Response,
    quote_id: int,
    body: EvaluateIn,
    x_quote_token: str = Header(..., alias="X-Quote-Token"),
) -> EvaluateOut:
    pool = db.get_pool()
    await _require_quote_ownership(pool, quote_id, x_quote_token)

    # evaluate_and_record_quote() only returns the outcome, not evaluated_at - both
    # statements run on the same connection/transaction so the second one is guaranteed to
    # see the row the first one just inserted, no race with a concurrent evaluation of the
    # same quote_id/strategy.
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                outcome = await conn.fetchval(
                    "SELECT evaluate_and_record_quote($1, $2)", quote_id, body.strategy
                )
            except asyncpg.exceptions.RaiseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            evaluated_at = await conn.fetchval(
                """
                SELECT evaluated_at FROM quote_evaluation
                WHERE quote_id = $1 AND strategy = $2
                ORDER BY evaluated_at DESC, id DESC
                LIMIT 1
                """,
                quote_id,
                body.strategy,
            )

            # which rule decided it, and which answer(s) drove it - see
            # sql/phase8_evaluation_trigger.sql in the underwritting repo. Not persisted
            # (quote_evaluation only ever stores the outcome), just computed alongside it.
            rule_id = await conn.fetchval(
                "SELECT uw_evaluation_trigger($1, $2)", quote_id, body.strategy
            )

            trigger = None
            if rule_id is not None:
                rule_name = await conn.fetchval("SELECT name FROM uw_rule WHERE id = $1", rule_id)
                condition_rows = await conn.fetch(
                    """
                    SELECT q.code
                    FROM uw_rule_condition rc
                    JOIN uw_question q ON q.id = rc.question_id
                    WHERE rc.rule_id = $1
                    ORDER BY rc.id
                    """,
                    rule_id,
                )
                # a rule can't have two conditions on the same question in practice, but
                # dict.fromkeys dedupes defensively while preserving definition order
                question_codes = list(dict.fromkeys(row["code"] for row in condition_rows))

                stopped_early = False
                if body.strategy == "short_circuit":
                    stop_rule_id = await conn.fetchval(
                        "SELECT uw_short_circuit_stop_rule($1)", quote_id
                    )
                    stopped_early = stop_rule_id == rule_id

                trigger = TriggerOut(
                    rule_name=rule_name, question_codes=question_codes, stopped_early=stopped_early
                )

    return EvaluateOut(
        quote_id=quote_id,
        strategy=body.strategy,
        outcome=outcome,
        evaluated_at=evaluated_at,
        trigger=trigger,
    )
