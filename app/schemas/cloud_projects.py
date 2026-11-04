import re

from pydantic import Field, field_validator

from app.schemas.coordination import Contract


class PrepareCloudEnvironment(Contract):
    request_id: str = Field(pattern=r'^[a-fA-F0-9-]{32,36}$')
    agent_id: str = 'optimus'
    name: str = Field(min_length=1, max_length=255)
    repository: str = Field(min_length=3, max_length=201)
    branch: str = Field(default='main', min_length=1, max_length=200)
    check: str = Field(default='git --version', min_length=1, max_length=4000)

    @field_validator('repository')
    @classmethod
    def github_repository(cls, value):
        value = value.strip().removeprefix('https://github.com/').removesuffix('.git')
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_.-]{1,100}', value)
                or value.split('/')[-1] in {'.', '..'}):
            raise ValueError('Enter a GitHub repository as owner/name')
        return value.lower()

    @field_validator('branch')
    @classmethod
    def branch_ref(cls, value):
        if (value == '@' or value.startswith('-') or value.endswith('.') or '..' in value or '@{' in value
                or any(ord(character) <= 32 or ord(character) == 127 or character in '~^:?*[\\' for character in value)
                or any(not part or part.startswith('.') or part.endswith('.lock')
                       for part in value.split('/'))):
            raise ValueError('Enter a valid Git branch name')
        return value


class PublishCloudEnvironment(Contract):
    environment_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    review_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class CloudPullRequest(Contract):
    task_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    expected_source_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_artifact_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default='', max_length=8000)
