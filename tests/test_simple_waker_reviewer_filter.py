def test_filter_topic_progress_reviewer_contextual_muted() -> None:
    from cli.simple_waker import _filter_topic_progress_for_persona

    data = {
        "items": [
            {
                "topic_id": "t1",
                "work_items": [{"priority": "contextual", "kind": "unread_change"}],
            },
            {
                "topic_id": "t2",
                "work_items": [{"priority": "obligation", "kind": "mention"}],
            },
        ],
        "total": 2,
    }
    todos = {"pending_result_reviews": [{"id": "e1"}]}
    filtered = _filter_topic_progress_for_persona("reviewer", data, todos)
    assert filtered is not None
    assert filtered["total"] == 1
    assert filtered["items"][0]["topic_id"] == "t2"
