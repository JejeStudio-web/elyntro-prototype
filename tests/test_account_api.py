import pytest

from account_api import _merge_payload, _validate_payload


def test_sync_merge_keeps_newest_conversation_and_deletion_tombstones():
    old = {"activeId":"a","items":[{"id":"a","title":"old","updatedAt":10,"messages":[{"role":"user","content":"old"}]},{"id":"b","title":"remove me","updatedAt":5,"messages":[]}],"deleted":{}}
    new = {"activeId":"a","items":[{"id":"a","title":"new","updatedAt":20,"messages":[{"role":"user","content":"new"}]}],"deleted":{"b":6}}
    merged = _merge_payload(old, new)
    assert [item["id"] for item in merged["items"]] == ["a"]
    assert merged["items"][0]["title"] == "new"
    assert merged["deleted"]["b"] == 6


def test_sync_rejects_duplicate_conversation_ids_and_oversized_messages():
    with pytest.raises(ValueError):
        _validate_payload({"items":[{"id":"x","messages":[]},{"id":"x","messages":[]}]})
    with pytest.raises(ValueError):
        _validate_payload({"items":[{"id":"x","messages":[{"role":"user","content":"x"*12001}]}]})


def test_sync_rejects_more_than_100_conversations():
    with pytest.raises(ValueError):
        _validate_payload({"items":[{"id":str(i),"messages":[]} for i in range(101)]})
