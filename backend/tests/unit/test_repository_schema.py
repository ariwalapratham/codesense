from schemas import RepositorySchema


def test_repository_schema_defaults_to_shared_public():
    repo = RepositorySchema(
        owner="acme",
        repo_name="demo",
        repository_url="https://github.com/acme/demo.git",
        branch="main",
    )
    dumped = repo.model_dump()
    assert dumped["visibility"] == "public"
    assert dumped["owner_user_id"] is None
    assert dumped["branch"] == "main"
