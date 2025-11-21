# app.py
import base64
import logging
import os
from uuid import uuid4
from typing import List, Dict, Any

import boto3
from botocore.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from boto3.dynamodb.conditions import Key

# Config / environment
REGION = os.environ.get("AWS_REGION", "us-east-2")
BUCKET = os.environ.get("UPLOAD_BUCKET", "site-cloud25f-uploads")
TABLE = os.environ.get("IMAGES_TABLE", "Images")
PRESIGN_EXPIRES = int(os.environ.get("PRESIGN_EXPIRES", "300"))

# Boto3 clients/resources (will use instance role by default)
boto_config = Config(retries={"max_attempts": 3, "mode": "standard"})
s3 = boto3.client("s3", region_name=REGION, config=boto_config)
ddb_res = boto3.resource("dynamodb", region_name=REGION, config=boto_config)
images_table = ddb_res.Table(TABLE)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cloud25f-api")

app = FastAPI(title="Cloud25F Image API")

# Allow CORS (adjust origin for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # trocar para domínio do ALB em produção
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

class PresignRequest(BaseModel):
    filename: str = None
    contentType: str = "image/png"

class PresignResponse(BaseModel):
    url: str
    key: str
    expires: int

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/presign", response_model=PresignResponse)
def presign(req: PresignRequest):
    # gerar filename se não fornecido
    filename = req.filename or f"{uuid4().hex}.png"
    key = f"uploads/{filename}"
    content_type = req.contentType or "image/png"

    try:
        url = s3.generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket": BUCKET, "Key": key, "ContentType": content_type},
            ExpiresIn=PRESIGN_EXPIRES,
        )
        logger.info("Generated presign for key=%s", key)
        return {"url": url, "key": key, "expires": PRESIGN_EXPIRES}
    except Exception as e:
        logger.exception("Failed to generate presign")
        raise HTTPException(status_code=500, detail="Could not generate presigned URL")

@app.get("/images", response_model=List[Dict[str, Any]])
def list_images(limit: int = 50, last_evaluated_key: str = None):
    """
    Lista apenas os itens META para cada imageId.
    Nota: para simplicidade usamos scan (pode ser caro). Em produção, crie um GSI para metadados.
    """
    try:
        kwargs = {"Limit": limit}
        if last_evaluated_key:
            # last_evaluated_key should be provided as JSON string if used (omitted here for brevity)
            pass

        resp = images_table.scan(ProjectionExpression="imageId, sk, contentType, sizeBytes, totalChunks")
        items = resp.get("Items", [])
        metas = [it for it in items if it.get("sk") == "META"]
        result = []
        for m in metas:
            result.append({
                "imageId": m.get("imageId"),
                "contentType": m.get("contentType"),
                "sizeBytes": int(m.get("sizeBytes")) if m.get("sizeBytes") else None,
                "totalChunks": int(m.get("totalChunks")) if m.get("totalChunks") else None
            })
        return result
    except Exception:
        logger.exception("Failed to list images")
        raise HTTPException(status_code=500, detail="Failed to list images")

@app.get("/images/{image_id}")
def get_image(image_id: str):
    """
    Recupera todos os items com PK=imageId, ordena por sk (CHUNK#*) e concatena data.
    Retorna JSON: { contentType, base64 }.
    """
    try:
        # Query usando KeyConditionExpression para performance
        resp = images_table.query(
            KeyConditionExpression=Key("imageId").eq(image_id)
        )
        items = resp.get("Items", [])
        if not items:
            raise HTTPException(status_code=404, detail="Image not found")

        # achar META e CHUNKS
        meta = next((i for i in items if i.get("sk") == "META"), None)
        chunks = sorted([i for i in items if str(i.get("sk")).startswith("CHUNK#")], key=lambda x: x.get("sk"))
        if not meta:
            raise HTTPException(status_code=500, detail="Metadata missing for image")

        base64_parts = [c.get("data", "") for c in chunks]
        full_b64 = "".join(base64_parts)

        return {"contentType": meta.get("contentType"), "base64": full_b64}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to fetch image")
        raise HTTPException(status_code=500, detail="Failed to fetch image")
