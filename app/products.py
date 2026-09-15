"""Read-only endpoints for products and their questions.

`expected_answer` is deliberately never selected here - leaking it to a public,
applicant-facing client would hand them the answer key.
"""

from fastapi import APIRouter, HTTPException

from app import db
from app.models import ProductOut, QuestionOut

router = APIRouter()


@router.get("/products", response_model=list[ProductOut])
async def list_products() -> list[ProductOut]:
    pool = db.get_pool()
    rows = await pool.fetch("SELECT code, name, description FROM insurance_product ORDER BY code")
    return [ProductOut(**row) for row in rows]


@router.get("/products/{code}/questions", response_model=list[QuestionOut])
async def list_product_questions(code: str) -> list[QuestionOut]:
    pool = db.get_pool()

    product = await pool.fetchrow("SELECT id FROM insurance_product WHERE code = $1", code)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown product code: {code}")

    rows = await pool.fetch(
        """
        SELECT q.code AS question_code, q.text, q.answer_type, q.enum_options,
               pq.sequence, pq.is_mandatory
        FROM product_question pq
        JOIN uw_question q ON q.id = pq.question_id
        WHERE pq.product_id = $1
        ORDER BY pq.sequence
        """,
        product["id"],
    )
    return [QuestionOut(**row) for row in rows]
