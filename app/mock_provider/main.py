"""OpenAI 호환 Mock Provider (인메모리 상태)."""
import json
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.contract import MOCK_USAGE_LAG_S, WIF_TOKEN_TTL_S, cost_krw

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

reqs: dict[str, list[tuple[float, int]]] = {}  # owner -> [(시각, 토큰)]
revoked: set[str] = set()
sts: dict[str, tuple[str, float]] = {}  # 토큰 -> (owner, 만료 epoch)


def _reset():
    reqs.clear(); revoked.clear(); sts.clear()


def _owner(request: Request):
    """Bearer -> owner. 폐기/만료/누락이면 None."""
    key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if key in sts:
        owner, exp = sts[key]
        if time.time() >= exp:
            return None
    else:
        owner = key
    return None if not owner or owner in revoked else owner


def _revoked():
    return JSONResponse({"error": "revoked"}, status_code=401)


@app.post("/v1/chat/completions")
async def chat(request: Request):
    owner = _owner(request)
    if owner is None:
        return _revoked()
    body = await request.json()
    n = int(body.get("max_tokens") or 300)
    reqs.setdefault(owner, []).append((time.time(), n))
    model, cid, now = body.get("model", "mock"), "chatcmpl-" + secrets.token_hex(8), int(time.time())
    usage = {"prompt_tokens": 0, "completion_tokens": n, "total_tokens": n}
    if not body.get("stream"):
        return {"id": cid, "object": "chat.completion", "created": now, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "mock response"},
                             "finish_reason": "stop"}], "usage": usage}

    def gen():
        def chunk(delta, finish=None, **extra):
            c = {"id": cid, "object": "chat.completion.chunk", "created": now, "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], **extra}
            return f"data: {json.dumps(c)}\n\n"
        yield chunk({"role": "assistant", "content": "mock "})
        yield chunk({"content": "response"})
        yield chunk({}, "stop", usage=usage)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/usage")
def usage(owner: str):
    cutoff = time.time() - MOCK_USAGE_LAG_S
    rows = [r for r in reqs.get(owner, []) if r[0] <= cutoff]
    t = sum(r[1] for r in rows)
    return {"owner": owner, "cost_krw": cost_krw(t), "tokens": t, "requests": len(rows)}


@app.post("/revoke")
async def revoke(request: Request):
    revoked.add((await request.json())["owner"])
    return {"ok": True}


@app.post("/sts/token")
async def sts_token(request: Request):
    aud = (await request.json())["audience"]
    owner = f"wif:{aud}"
    if owner in revoked:
        return _revoked()
    tok = "wif-" + secrets.token_urlsafe(24)
    sts[tok] = (owner, time.time() + WIF_TOKEN_TTL_S)
    return {"token": tok, "expires_in": WIF_TOKEN_TTL_S, "owner": owner}


@app.post("/demo/reset")
def demo_reset():
    _reset()
    return {"ok": True}
