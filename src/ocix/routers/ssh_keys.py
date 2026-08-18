import base64
import hashlib
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import security
from ..db import (
    audit,
    create_ssh_key,
    delete_ssh_key,
    get_ssh_key,
    list_ssh_keys,
    update_ssh_key,
)
from ..schemas import SSHKeyCreateRequest, SSHKeyUpdateRequest

router = APIRouter(prefix="/api/ssh-keys", tags=["ssh-keys"])


def _calculate_fingerprint(public_key: str) -> tuple[str, str]:
    """解析公钥类型与 SHA256 指纹。

    返回 (key_type, fingerprint)
    """
    parts = public_key.strip().split()
    if not parts:
        return "", ""
    key_type = parts[0]
    if len(parts) < 2:
        return key_type, ""
    b64_blob = parts[1]
    pad = len(b64_blob) % 4
    if pad:
        b64_blob += "=" * (4 - pad)
    try:
        raw_bytes = base64.b64decode(b64_blob)
        digest = hashlib.sha256(raw_bytes).digest()
        fp = "SHA256:" + base64.b64encode(digest).decode("utf-8").rstrip("=")
        return key_type, fp
    except Exception:
        return key_type, ""


@router.get("")
def get_all_keys(
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    keys = list_ssh_keys()
    # 增加 key_type 辅助字段
    res = []
    for k in keys:
        ktype, fp = _calculate_fingerprint(k["public_key"])
        item = dict(k)
        item["key_type"] = ktype
        if not item.get("fingerprint") and fp:
            item["fingerprint"] = fp
        res.append(item)
    return {"keys": res}


@router.post("")
def add_key(
    req: SSHKeyCreateRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    ktype, fp = _calculate_fingerprint(req.public_key)
    try:
        key_id = create_ssh_key(req.name, req.public_key, fp)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"已存在同名公钥备注「{req.name}」，请换一个名称")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"保存公钥失败: {e}")

    audit(user, "create-ssh-key", target=req.name, detail=f"id={key_id} fp={fp}", result="ok", ip=ip)
    return {
        "ok": True,
        "key": {
            "id": key_id,
            "name": req.name,
            "public_key": req.public_key,
            "fingerprint": fp,
            "key_type": ktype,
        },
    }


@router.put("/{key_id}")
def edit_key(
    key_id: int,
    req: SSHKeyUpdateRequest,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    existing = get_ssh_key(key_id)
    if not existing:
        raise HTTPException(status_code=404, detail="公钥不存在")

    ktype, fp = _calculate_fingerprint(req.public_key)
    try:
        update_ssh_key(key_id, req.name, req.public_key, fp)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"已存在同名公钥备注「{req.name}」，请换一个名称")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"更新公钥失败: {e}")

    audit(user, "update-ssh-key", target=req.name, detail=f"id={key_id} fp={fp}", result="ok", ip=ip)
    return {
        "ok": True,
        "key": {
            "id": key_id,
            "name": req.name,
            "public_key": req.public_key,
            "fingerprint": fp,
            "key_type": ktype,
        },
    }


@router.delete("/{key_id}")
def remove_key(
    key_id: int,
    request: Request,
    user: str = Depends(security.get_current_user),
):
    security.check_rate(request, security.API_RATE_LIMIT)
    ip = security.client_ip(request)
    existing = get_ssh_key(key_id)
    if not existing:
        raise HTTPException(status_code=404, detail="公钥不存在")

    delete_ssh_key(key_id)
    audit(user, "delete-ssh-key", target=existing["name"], detail=f"id={key_id}", result="ok", ip=ip)
    return {"ok": True}
