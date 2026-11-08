"""Account-owned GitHub targets for Cloud Projects. All credentials stay host-side."""
import asyncio
import re

from app.schemas.cloud_projects import PrepareCloudEnvironment
from app.services.coordination_budget import CoordinationError


class CloudGitHub:
    def __init__(self, cloud):
        self.cloud = cloud
        self.settings = cloud.projects.settings

    def credentials(self):
        account = self.settings.optimus_github_account.strip().lower()
        email = self.settings.optimus_github_commit_email.strip()
        token = self.settings.optimus_github_workspace_token.get_secret_value().strip()
        if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,38}', account) or not token
                or not re.fullmatch(r'[A-Za-z0-9_.+-]+@users\.noreply\.github\.com', email)):
            raise CoordinationError('cloud_github_setup', 'Configure the Optimus GitHub account, credential and GitHub noreply email', 503)
        return account, email, token

    async def identity(self):
        account, _, token = self.credentials()
        result, _ = await self.cloud.github_api('GET', '/user', token)
        if (not isinstance(result, dict) or str(result.get('login', '')).lower() != account
                or type(result.get('id')) is not int):
            raise CoordinationError('cloud_github_identity', 'The GitHub credential did not authenticate as the configured Optimus account', 403)
        return account, result['id'], token

    async def repository(self, repository, token):
        result, error = await self.cloud.github_api('GET', '/repos/' + repository, token)
        if getattr(error, 'status_code', None) == 404:
            return None
        if not isinstance(result, dict):
            raise CoordinationError('cloud_github_lookup', error.replace(token, '[redacted]') or 'GitHub repository lookup failed')
        return result

    @staticmethod
    def verify_target(repo, target):
        if (str(repo.get('full_name', '')).lower() != target['repository']
                or (repo.get('owner') or {}).get('id') != target['account_id']
                or type(repo.get('id')) is not int
                or repo.get('private') is not target['private']
                or repo.get('archived') or repo.get('disabled')):
            raise CoordinationError('cloud_github_target', 'GitHub destination ownership or visibility differs from the saved project')
        if target['mode'] == 'fork':
            if repo.get('fork') is not True or (repo.get('parent') or {}).get('id') != target['upstream_id']:
                raise CoordinationError('cloud_github_fork', 'The GitHub repository is not a verified fork of this upstream')
        elif repo.get('fork') or repo.get('description') != target['marker']:
            raise CoordinationError('cloud_github_target', 'The GitHub repository does not belong to this prototype request')
        if target.get('id') is not None and target['id'] != repo['id']:
            raise CoordinationError('cloud_github_target', 'The GitHub repository ID changed')

    async def provision(self, identity, details):
        account, account_id, token = await self.identity()
        target = details.get('target')
        if target is None:
            if details['mode'] == 'prototype':
                name = (re.sub(r'[^a-z0-9]+', '-', details['name'].lower()).strip('-')[:70] or 'prototype')
                target = {'repository': f'{account}/{name}-{identity[:10]}', 'private': True,
                    'marker': f'Optimus Cloud Project {identity}'}
            else:
                upstream = await self.repository(details['repository'], token)
                if not upstream or type(upstream.get('id')) is not int:
                    raise CoordinationError('cloud_github_upstream', 'Optimus cannot read the upstream repository', 404)
                repository = PrepareCloudEnvironment.github_repository(upstream['full_name'])
                if repository.split('/')[0] == account:
                    raise CoordinationError('cloud_github_upstream', 'Use the existing repository flow for a repo Optimus already owns')
                private = upstream.get('private') is True
                if upstream.get('visibility') == 'internal' or (not private and not details['allow_public']):
                    raise CoordinationError('cloud_fork_visibility', 'A public fork requires permission to publish public checkpoints. Internal forks are unavailable.', 422)
                target = {'repository': f"{account}/{repository.split('/')[1]}", 'private': private,
                    'upstream': repository, 'upstream_id': upstream['id'],
                    'branch': PrepareCloudEnvironment.branch_ref(upstream['default_branch'])}
            target = {**target, 'mode': details['mode'], 'account_id': account_id}
        if target['account_id'] != account_id:
            raise CoordinationError('cloud_github_identity', 'The configured GitHub account changed')
        repo = await self.repository(target['repository'], token)
        if repo is None and details.get('phase') != 'repository_requested':
            # Persist the exact destination before making any remote write.
            await self.cloud.settle(identity, 'preparing', {**details, 'target': target, 'phase': 'repository_requested'})
            name = target['repository'].split('/')[1]
            if target['mode'] == 'prototype':
                path, body = '/user/repos', {'name': name, 'description': target['marker'], 'private': True, 'auto_init': True}
            else:
                path, body = '/repos/' + target['upstream'] + '/forks', {'name': name, 'default_branch_only': True}
            result, error = await self.cloud.github_api('POST', path, token, json=body)
            if not isinstance(result, dict):
                raise CoordinationError('cloud_github_provision', error.replace(token, '[redacted]') or 'GitHub did not confirm repository creation')
            for attempt in range(8):
                repo = await self.repository(target['repository'], token)
                if repo is not None and (target['mode'] == 'prototype' or (repo.get('parent') or {}).get('id')):
                    break
                if attempt < 7:
                    await asyncio.sleep(2)
        if repo is None:
            raise CoordinationError('cloud_github_pending', 'GitHub has not confirmed the repository yet', 409)
        self.verify_target(repo, target)
        branch = target.get('branch') or PrepareCloudEnvironment.branch_ref(repo['default_branch'])
        return {**target, 'id': repo['id'], 'branch': branch, 'url': 'https://github.com/' + target['repository']}

    async def verify_write(self, target):
        _, account_id, token = await self.identity()
        if account_id != target['account_id']:
            raise CoordinationError('cloud_github_identity', 'The configured GitHub account changed', 403)
        repo = await self.repository(target['repository'], token)
        if repo is None:
            raise CoordinationError('cloud_github_target', 'The saved GitHub destination is unavailable', 404)
        self.verify_target(repo, target)
        return token
