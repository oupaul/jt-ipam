"""憑證管理 API 測試：建立、上傳版本(驗證+加密)、列表、RBAC。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.models.user import User
from app.services.auth import issue_access_token
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _make_cert(cn="example.com", sans=("example.com",), days=90):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=400)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in sans]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()).decode()
    return cert_pem, key_pem


def _files(cert_pem, key_pem):
    return {
        "cert_file": ("cert.crt", cert_pem, "application/x-pem-file"),
        "key_file": ("cert.key", key_pem, "application/x-pem-file"),
    }


async def _create_cert(client, auth_headers, name=None) -> str:
    name = name or f"cert-{uuid.uuid4().hex[:6]}"
    r = await client.post("/api/v1/certificates", headers=auth_headers,
                          json={"name": name, "description": "d"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_create_and_upload_version(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert(sans=("a.example.com", "b.example.com"))
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, key_pem))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_current"] is True
    assert len(body["fingerprint_sha256"]) == 64
    assert set(body["domains"]) == {"a.example.com", "b.example.com"}
    # 私鑰一律不在回應
    assert "key" not in body
    assert "key_enc" not in body

    # 列表帶出目前版本摘要
    lst = await client.get("/api/v1/certificates", headers=auth_headers)
    item = next(c for c in lst.json()["items"] if c["id"] == cid)
    assert item["current_fingerprint"] == body["fingerprint_sha256"]
    assert item["version_count"] == 1


async def test_upload_key_mismatch_400(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, _ = _make_cert()
    _, other_key = _make_cert()
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, other_key))
    assert r.status_code == 400


async def test_upload_expired_400(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert(days=-2)
    r = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                          files=_files(cert_pem, key_pem))
    assert r.status_code == 400


async def test_upload_duplicate_fingerprint_409(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    cert_pem, key_pem = _make_cert()
    r1 = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                           files=_files(cert_pem, key_pem))
    assert r1.status_code == 201
    r2 = await client.post(f"/api/v1/certificates/{cid}/versions", headers=auth_headers,
                           files=_files(cert_pem, key_pem))
    assert r2.status_code == 409


async def test_generate_self_signed_version(client, auth_headers):
    cid = await _create_cert(client, auth_headers)
    r = await client.post(f"/api/v1/certificates/{cid}/self-signed", headers=auth_headers,
                          json={"common_name": "lab.lan", "sans": ["lab.lan", "x.lan"], "days": 30})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_current"] is True
    assert set(body["domains"]) == {"lab.lan", "x.lan"}
    assert "key" not in body
    # 自簽版本可被 agent 派送：列表會顯示為目前版本
    lst = await client.get("/api/v1/certificates", headers=auth_headers)
    item = next(c for c in lst.json()["items"] if c["id"] == cid)
    assert item["current_fingerprint"] == body["fingerprint_sha256"]


async def test_requires_admin(client, db_session):
    u = User(username=f"na-{uuid.uuid4().hex[:6]}", email=f"{uuid.uuid4().hex[:6]}@t.local",
             display_name="NA", password_hash=hash_password("TestPassword2026!"),
             auth_provider="local", is_active=True, is_admin=False)
    db_session.add(u)
    await db_session.commit()
    token = issue_access_token(u)
    r = await client.get("/api/v1/certificates", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
