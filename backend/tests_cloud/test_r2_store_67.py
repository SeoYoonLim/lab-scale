"""67차 R2Store(boto3) 단위 테스트. botocore Stubber로 응답을 흉내 낸다(네트워크·실제 R2 없음).

boto3는 requirements-cloud.txt에만 있으므로 기본 tests/에 두지 않는다. 실행:
  pip install -r requirements.txt -r requirements-cloud.txt
  python -m pytest tests_cloud -q -c /dev/null --rootdir . -p no:cacheprovider     (workflow session-minute-cloud.yml cloud-unit)
"""
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from botocore.stub import Stubber  # noqa: E402

from scripts import run_cloud_session_preservation as c  # noqa: E402

ENV = {"R2_ACCOUNT_ID": "0123456789abcdef0123456789abcdef", "R2_ACCESS_KEY_ID": "dummy-access-key-id-example",
       "R2_SECRET_ACCESS_KEY": "dummy-secret-example-not-real", "R2_BUCKET_NAME": "minute-bucket"}


@pytest.fixture
def store():
    s = c.R2Store(ENV)
    with Stubber(s.c) as stub:
        s.stub = stub
        yield s
        stub.assert_no_pending_responses()


def test_endpoint_region_and_checksum_config():
    s = c.R2Store(ENV)
    assert s.c.meta.endpoint_url == "https://0123456789abcdef0123456789abcdef.r2.cloudflarestorage.com"
    assert s.c.meta.region_name == "auto" and s.bucket == "minute-bucket"
    assert s.c.meta.config.request_checksum_calculation == "when_required"
    assert s.c.meta.config.response_checksum_validation == "when_required"


def test_head_missing_is_none_and_present_returns_size_and_sha(store):
    store.stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                                expected_params={"Bucket": "minute-bucket", "Key": "session-minute/2026-11-16/manifest.json"})
    assert store.head("session-minute/2026-11-16/manifest.json") is None
    store.stub.add_response("head_object", {"ContentLength": 12, "Metadata": {"sha256": "ab" * 32}},
                            {"Bucket": "minute-bucket", "Key": "k"})
    assert store.head("k") == {"size": 12, "sha256": "ab" * 32}


def test_put_sends_sha256_metadata_and_no_public_acl(store):
    data = b"[1,2,3]"
    sha = hashlib.sha256(data).hexdigest()
    store.stub.add_response("put_object", {}, {"Bucket": "minute-bucket", "Key": "session-minute/x/raw/005930.json", "Body": data,
                                               "ContentType": "application/json", "Metadata": {"sha256": sha}})
    store.put("session-minute/x/raw/005930.json", data, sha, "application/json")  # ACL 인자가 있으면 Stubber가 거부한다


def test_auth_errors_become_store_auth_error(store):
    store.stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
    with pytest.raises(c.StoreAuthError):
        store.head("k")
    store.stub.add_client_error("put_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(c.StoreAuthError):
        store.put("k", b"x", hashlib.sha256(b"x").hexdigest(), "text/plain")


def test_list_paginates(store):
    store.stub.add_response("list_objects_v2", {"Contents": [{"Key": "p/a"}], "IsTruncated": True, "NextContinuationToken": "t"},
                            {"Bucket": "minute-bucket", "Prefix": "p/"})
    store.stub.add_response("list_objects_v2", {"Contents": [{"Key": "p/b"}], "IsTruncated": False},
                            {"Bucket": "minute-bucket", "Prefix": "p/", "ContinuationToken": "t"})
    assert store.list("p/") == ["p/a", "p/b"]


def test_secrets_not_in_repr_or_errors(store):
    store.stub.add_client_error("get_object", service_error_code="AccessDenied", http_status_code=403)
    with pytest.raises(c.StoreAuthError) as ei:
        store.get("k")
    assert ENV["R2_SECRET_ACCESS_KEY"] not in str(ei.value) and ENV["R2_ACCESS_KEY_ID"] not in str(ei.value)
