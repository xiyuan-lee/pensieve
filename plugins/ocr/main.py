import asyncio
import logging
from typing import Optional
import httpx
import json
import base64
import io
import os
from PIL import Image

from fastapi import FastAPI, Request, HTTPException
from memos.schemas import Entity, MetadataType

METADATA_FIELD_NAME = "ocr_result"
PLUGIN_NAME = "ocr"

app = FastAPI()

endpoint = None
token = None
semaphore = asyncio.Semaphore(4)

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def image2base64(img_path):
    try:
        with Image.open(img_path) as img:
            img.convert("RGB")  # Check if image is not broken
        with open(img_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return encoded_string
    except Exception as e:
        logger.error(f"Error processing image {img_path}: {str(e)}")
        return None


async def fetch(endpoint: str, client, image_base64, headers: Optional[dict] = None):
    async with semaphore:  # 使用信号量控制并发
        response = await client.post(
            f"{endpoint}",
            json={"image_base64": image_base64},
            timeout=60,
            headers=headers,
        )
        if response.status_code != 200:
            return None
        return response.json()


async def predict(img_path):
    image_base64 = image2base64(img_path)
    if not image_base64:
        return None

    async with httpx.AsyncClient() as client:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        ocr_result = await fetch(endpoint, client, image_base64, headers=headers)
        return ocr_result


app = FastAPI()


@app.get("/")
async def read_root():
    return {"healthy": True}


@app.post("/")
async def ocr(entity: Entity, request: Request):
    if not entity.file_type_group == "image":
        return {METADATA_FIELD_NAME: "{}"}

    # Get the URL to patch the entity's metadata from the "Location" header
    location_url = request.headers.get("Location")
    if not location_url:
        raise HTTPException(status_code=400, detail="Location header is missing")

    patch_url = f"{location_url}/metadata"

    ocr_result = await predict(entity.filepath)

    print(ocr_result)
    if ocr_result is None or not ocr_result:
        print(f"No OCR result found for file: {entity.filepath}")
        return {METADATA_FIELD_NAME: "{}"}

    # Call the URL to patch the entity's metadata
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            patch_url,
            json={
                "metadata_entries": [
                    {
                        "key": METADATA_FIELD_NAME,
                        "value": json.dumps(
                            ocr_result,
                            default=lambda o: o.item() if hasattr(o, "item") else o,
                        ),
                        "source": PLUGIN_NAME,
                        "data_type": MetadataType.JSON_DATA.value,
                    }
                ]
            },
        )

    # Check if the patch request was successful
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code, detail="Failed to patch entity metadata"
        )

    return {
        METADATA_FIELD_NAME: json.dumps(
            ocr_result,
            default=lambda o: o.item() if hasattr(o, "item") else o,
        )
    }


if __name__ == "__main__":
    import uvicorn
    import argparse

    parser = argparse.ArgumentParser(description="OCR Plugin")
    parser.add_argument(
        "--endpoint",
        type=str,
        required=True,
        help="The endpoint URL for the OCR service",
    )
    parser.add_argument(
        "--token", type=str, required=False, help="The token for authentication"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="The port number to run the server on"
    )

    args = parser.parse_args()
    endpoint = args.endpoint
    token = args.token
    port = args.port

    uvicorn.run(app, host="0.0.0.0", port=port)
