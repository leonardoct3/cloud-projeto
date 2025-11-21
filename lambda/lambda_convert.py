# lambda_convert.py
import os
import base64
import logging
import math
import boto3
from botocore.exceptions import ClientError

# opcional: carregar .env localmente se estiver no ambiente de desenvolvimento
try:
    # python-dotenv é opcional; não está disponível por padrão no Lambda
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# logging
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# configurações via env
IMAGES_TABLE = os.environ.get("IMAGES_TABLE", "Images")
UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "site-cloud25f-uploads")
DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "anonymous")
# CHUNK_SIZE pode ser definido em caracteres (Base64 chars). Valor padrão seguro:
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", str(350_000)))

# AWS clients/resources
s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(IMAGES_TABLE)


def _safe_image_id(key: str) -> str:
    """Gera image_id seguro a partir do key do S3."""
    return key.replace("/", "__")


def _get_user_from_metadata(s3_head_resp: dict) -> str:
    """
    Tenta extrair userId dos metadados do objeto S3.
    Metadados custom ficam em s3.get_object()['Metadata'] com chaves em minúsculas.
    Ex.: 'x-amz-meta-userid' no request vira 'userid' aqui.
    """
    meta = s3_head_resp.get("Metadata", {}) or {}
    user = meta.get("userid") or meta.get("user") or meta.get("owner") or None
    if user:
        return user
    return DEFAULT_USER_ID


def _put_meta_item(image_id: str, user_id: str, content_type: str, size_bytes: int, total_chunks: int):
    """Grava o item META na tabela DynamoDB (substitui se já existir)."""
    meta_item = {
        "userId": user_id,
        "sk": f"{image_id}#META",
        "contentType": content_type,
        "sizeBytes": size_bytes,
        "totalChunks": int(total_chunks),
    }
    logger.info("Put META item: user=%s image=%s chunks=%d", user_id, image_id, total_chunks)
    table.put_item(Item=meta_item)


def _put_chunk_items(image_id: str, user_id: str, chunks):
    """
    Grava os itens CHUNK usando batch_writer para maior eficiência.
    Cada chunk vira um item com sk = "<image_id>#CHUNK#nnnn".
    """
    logger.info("Gravando %d chunks no DynamoDB para image=%s", len(chunks), image_id)
    with table.batch_writer() as batch:
        for idx, chunk in enumerate(chunks):
            sk = f"{image_id}#CHUNK#{idx+1:04d}"  # 1-indexed e zero-padded
            item = {
                "userId": user_id,
                "sk": sk,
                "chunkIndex": int(idx),
                "data": chunk
            }
            batch.put_item(Item=item)
    logger.info("Chunks gravados com sucesso.")


def lambda_handler(event, context):
    """
    Handler da Lambda que processa eventos S3:ObjectCreated:Put
    - Lê objeto do S3
    - Converte para Base64
    - Faz chunking em CHUNK_SIZE caracteres
    - Grava META + CHUNKs no DynamoDB com PK=userId e SK ordenável
    """
    logger.info("Evento recebido: %s", event)

    records = event.get("Records", [])
    results = []

    for rec in records:
        try:
            s3_info = rec.get("s3", {})
            bucket = s3_info.get("bucket", {}).get("name")
            key = s3_info.get("object", {}).get("key")
            if not bucket or not key:
                logger.warning("Registro S3 sem bucket/key: %s", rec)
                continue

            logger.info("Processando objeto S3: bucket=%s key=%s", bucket, key)
            # get object
            resp = s3.get_object(Bucket=bucket, Key=key)
            body = resp["Body"].read()
            content_type = resp.get("ContentType") or "application/octet-stream"
            size_bytes = int(resp.get("ContentLength", len(body)))

            # identificar usuário a partir de metadados
            user_id = _get_user_from_metadata(resp)
            image_id = _safe_image_id(key)

            # converter em Base64 (string)
            b64 = base64.b64encode(body).decode("utf-8")
            total_len = len(b64)
            total_chunks = math.ceil(total_len / CHUNK_SIZE)

            logger.info("Imagem convertida para Base64: len=%d chars total_chunks=%d", total_len, total_chunks)

            # preparar chunks (lista de strings)
            chunks = [b64[i:i + CHUNK_SIZE] for i in range(0, total_len, CHUNK_SIZE)]

            # gravar META
            _put_meta_item(image_id=image_id, user_id=user_id, content_type=content_type,
                           size_bytes=size_bytes, total_chunks=total_chunks)

            # gravar CHUNKs (batch)
            _put_chunk_items(image_id=image_id, user_id=user_id, chunks=chunks)

            results.append({
                "userId": user_id,
                "imageId": image_id,
                "totalChunks": total_chunks
            })
        except ClientError as e:
            logger.exception("Erro AWS ao processar registro: %s", e)
            # opcional: rethrow para forçar retry da Lambda se desejar
            # raise
        except Exception as e:
            logger.exception("Erro ao processar registro genérico: %s", e)
            # continue para processar outros registros
    logger.info("Processamento finalizado. Resultado: %s", results)
    return {"status": "ok", "processed": results}
