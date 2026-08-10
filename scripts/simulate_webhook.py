from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings

DEFAULT_URL = "http://localhost:8000/webhook/meta"


def sign(body: bytes) -> str:
    digest = hmac.new(settings.META_APP_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def send(payload: dict, url: str) -> None:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": sign(body),
    }
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            print(f"HTTP {response.status}: {response.read().decode('utf-8')}")
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8')}")


def build_comment_payload(ig_business_id: str, media_product_type: str, text: str) -> tuple[dict, str, str]:
    comment_id = f"test_comment_{uuid.uuid4().hex[:12]}"
    ig_user_id = f"test_user_{uuid.uuid4().hex[:8]}"
    media_id = f"test_media_{uuid.uuid4().hex[:12]}"
    payload = {
        "object": "instagram",
        "entry": [
            {
                "id": ig_business_id,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "text": text,
                            "media": {"id": media_id, "media_product_type": media_product_type},
                            "from": {"id": ig_user_id, "username": "test_customer"},
                        },
                    }
                ],
            }
        ],
    }
    return payload, comment_id, ig_user_id


def build_message_payload(ig_business_id: str, ig_user_id: str, text: str) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ig_business_id,
                "messaging": [
                    {
                        "sender": {"id": ig_user_id},
                        "recipient": {"id": ig_business_id},
                        "timestamp": int(time.time()),
                        "message": {"mid": f"test_mid_{uuid.uuid4().hex[:12]}", "text": text},
                    }
                ],
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Отправляет в локальный webhook-сервер LeadArmor подписанный "
        "payload, идентичный тому, что реально шлёт Meta — для теста без "
        "настоящего Instagram."
    )
    parser.add_argument("scenario", choices=["ad", "organic", "phone"])
    parser.add_argument("--ig-business-id", required=True, help="ig_business_id клиента из таблицы clients")
    parser.add_argument(
        "--ig-user-id",
        default=None,
        help="Только для scenario=phone — тот же ig_user_id, что был в ad-комментарии",
    )
    parser.add_argument("--text", default=None)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    if args.scenario == "ad":
        text = args.text or "цена?"
        payload, comment_id, ig_user_id = build_comment_payload(args.ig_business_id, "AD", text)
        print(f"comment_id={comment_id}")
        print(f"ig_user_id={ig_user_id}  <-- сохрани для scenario=phone")
    elif args.scenario == "organic":
        text = args.text or "цена?"
        payload, comment_id, ig_user_id = build_comment_payload(args.ig_business_id, "FEED", text)
        print(f"comment_id={comment_id}")
        print(f"ig_user_id={ig_user_id}")
    else:
        if not args.ig_user_id:
            parser.error("--ig-user-id обязателен для scenario=phone")
        text = args.text or "+998901234567"
        payload = build_message_payload(args.ig_business_id, args.ig_user_id, text)

    send(payload, args.url)


if __name__ == "__main__":
    main()
