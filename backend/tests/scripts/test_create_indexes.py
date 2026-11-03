from scripts.create_indexes import DuplicateGroup, DuplicateReport, format_duplicate_reports


def test_format_duplicate_reports_lists_groups():
    reports = [
        DuplicateReport(
            collection="csChunks",
            fields=("chunk_id",),
            groups=[DuplicateGroup(key={"chunk_id": "abc"}, count=3)],
        ),
        DuplicateReport(collection="csRepositories", fields=("owner", "repo_name")),
    ]
    text = format_duplicate_reports(reports)
    assert "csChunks (chunk_id): 1 group(s)" in text
    assert "{'chunk_id': 'abc'} x3" in text
    assert "csRepositories" not in text
    assert "Fix duplicates by hand" in text
