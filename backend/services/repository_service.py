"""Repository service for cloning GitHub and GitLab repositories with authentication."""

import re
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, quote

import httpx
from git import Repo, GitCommandError
from git.exc import InvalidGitRepositoryError

from config.settings import settings


class RepositoryService:
    """Service for cloning and managing Git repositories from GitHub and GitLab."""

    @staticmethod
    def _detect_repository_type(repo_url: str) -> str:
        """
        Detect if the repository URL is from GitHub or GitLab.

        Args:
            repo_url: The repository URL

        Returns:
            'github', 'gitlab', or 'unknown'
        """
        parsed = urlparse(repo_url)
        hostname = parsed.hostname or ""

        if "github.com" in hostname.lower():
            return "github"
        elif "gitlab.com" in hostname.lower() or "gitlab" in hostname.lower():
            return "gitlab"
        else:
            return "unknown"

    @staticmethod
    def _prepare_authenticated_url(repo_url: str, token: Optional[str]) -> str:
        """
        Prepare an authenticated URL by embedding the token.

        Args:
            repo_url: The original repository URL
            token: Personal access token for authentication

        Returns:
            Authenticated URL with token embedded
        """
        if not token:
            return repo_url

        # Handle HTTPS URLs
        if repo_url.startswith("https://"):
            # Remove existing token if present
            repo_url = re.sub(r"https://[^@]+@", "https://", repo_url)
            # Insert token
            repo_url = repo_url.replace("https://", f"https://{token}@")
        # Handle SSH URLs (convert to HTTPS with token)
        elif repo_url.startswith("git@"):
            # Convert git@github.com:user/repo.git to https://github.com/user/repo.git
            repo_url = repo_url.replace("git@", "https://").replace(":", "/")
            repo_url = repo_url.replace("https://", f"https://{token}@")

        return repo_url

    @staticmethod
    def _get_token_for_repo(repo_url: str) -> Optional[str]:
        """
        Get the appropriate token for the repository type.

        Args:
            repo_url: The repository URL

        Returns:
            Token string or None
        """
        repo_type = RepositoryService._detect_repository_type(repo_url)

        if repo_type == "github":
            return settings.github_token
        elif repo_type == "gitlab":
            return settings.gitlab_token
        else:
            return None

    @staticmethod
    def clone_repository(
        repo_url: str,
        local_dir: str,
        token: Optional[str] = None,
        branch: Optional[str] = None,
        depth: Optional[int] = None,
    ) -> Tuple[bool, str, Optional[Repo]]:
        """
        Clone a Git repository from GitHub or GitLab with authentication support.

        Args:
            repo_url: URL of the repository to clone (GitHub or GitLab)
            local_dir: Local directory path to clone the repository into
            token: Personal access token for authentication (optional, will use settings if not provided)
            branch: Specific branch to clone (optional)
            depth: Shallow clone depth (optional, for faster cloning)

        Returns:
            Tuple of (success: bool, message: str, repo: Optional[Repo])
        """
        try:
            # Convert to Path object for easier manipulation
            local_path = Path(local_dir)

            # Check if directory already exists and is not empty
            if local_path.exists() and any(local_path.iterdir()):
                # Check if it's already a git repository
                try:
                    existing_repo = Repo(local_path)
                    return (
                        True,
                        f"Repository already exists at {local_dir}",
                        existing_repo,
                    )
                except InvalidGitRepositoryError:
                    return (
                        False,
                        f"Directory {local_dir} exists but is not a git repository",
                        None,
                    )

            # Get token if not provided
            if not token:
                token = RepositoryService._get_token_for_repo(repo_url)

            # Prepare authenticated URL
            authenticated_url = RepositoryService._prepare_authenticated_url(
                repo_url, token
            )

            # Prepare clone arguments
            clone_kwargs = {"url": authenticated_url, "to_path": str(local_path)}

            if branch:
                clone_kwargs["branch"] = branch

            if depth:
                clone_kwargs["depth"] = depth

            # Clone the repository
            repo = Repo.clone_from(**clone_kwargs)

            return (
                True,
                f"Repository cloned successfully to {local_dir}",
                repo,
            )

        except GitCommandError as e:
            error_msg = str(e)
            if "Authentication failed" in error_msg or "fatal: could not read Username" in error_msg:
                return (
                    False,
                    f"Authentication failed. Please check your token for {RepositoryService._detect_repository_type(repo_url)}",
                    None,
                )
            elif "Repository not found" in error_msg or "fatal: repository" in error_msg.lower():
                return (
                    False,
                    f"Repository not found or access denied: {repo_url}",
                    None,
                )
            else:
                return (
                    False,
                    f"Error cloning repository: {error_msg}",
                    None,
                )

        except Exception as e:
            return (
                False,
                f"Unexpected error cloning repository: {str(e)}",
                None,
            )

    @staticmethod
    def pull_repository(local_dir: str, branch: Optional[str] = None) -> Tuple[bool, str]:
        """
        Pull latest changes from the remote repository.

        Args:
            local_dir: Local directory path of the repository
            branch: Specific branch to pull (optional, defaults to current branch)

        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            repo = Repo(local_dir)

            # Get the remote
            origin = repo.remotes.origin

            # Pull changes
            if branch:
                origin.pull(branch)
            else:
                origin.pull()

            return True, f"Repository updated successfully at {local_dir}"

        except InvalidGitRepositoryError:
            return False, f"{local_dir} is not a valid git repository"

        except GitCommandError as e:
            return False, f"Error pulling repository: {str(e)}"

        except Exception as e:
            return False, f"Unexpected error pulling repository: {str(e)}"

    @staticmethod
    def get_repository_info(local_dir: str) -> Optional[dict]:
        """
        Get information about a local repository.

        Args:
            local_dir: Local directory path of the repository

        Returns:
            Dictionary with repository information or None if not a valid repo
        """
        try:
            repo = Repo(local_dir)

            return {
                "is_dirty": repo.is_dirty(),
                "active_branch": str(repo.active_branch) if repo.head.is_valid() else None,
                "commit": repo.head.commit.hexsha[:7] if repo.head.is_valid() else None,
                "remote_urls": [remote.url for remote in repo.remotes],
                "branches": [str(branch) for branch in repo.branches],
            }

        except (InvalidGitRepositoryError, Exception):
            return None

    @staticmethod
    async def get_remote_commit_hash(
        repo_url: str, branch: Optional[str] = None
    ) -> Optional[str]:
        """
        Get the latest remote commit hash (short 7 characters) without cloning the repo.
        Works for GitHub and GitLab repositories.

        Args:
            repo_url: Repository URL
            branch: Branch name (optional)

        Returns:
            Short commit hash (7 characters) or None
        """
        repo_type = RepositoryService._detect_repository_type(repo_url)
        token = RepositoryService._get_token_for_repo(repo_url)

        parsed = urlparse(repo_url)
        # Remove .git suffix and split into path components
        path = parsed.path.strip("/").replace(".git", "")
        parts = path.split("/")

        if len(parts) < 2:
            return None

        owner, repo = parts[0], parts[1]

        if repo_type == "github":
            # GitHub API: GET /repos/{owner}/{repo}/commits?sha={branch}
            api_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
            params = {}
            if branch:
                params["sha"] = branch
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(api_url, headers=headers, params=params)
                    if response.status_code != 200:
                        return None
                    data = response.json()
                    # data can be a list of commits, latest first
                    if isinstance(data, list) and len(data) > 0 and "sha" in data[0]:
                        return data[0]["sha"][:7]
                except Exception:
                    return None

        elif repo_type == "gitlab":
            # GitLab API: GET /projects/:id/repository/commits?ref_name={branch}
            # project_id must be urlquoted (owner/repo)
            project_id = quote(f"{owner}/{repo}", safe="")
            api_url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/commits"
            params = {}
            if branch:
                params["ref_name"] = branch
            headers = {}
            if token:
                headers["PRIVATE-TOKEN"] = token

            async with httpx.AsyncClient() as client:
                try:
                    response = await client.get(api_url, headers=headers, params=params)
                    if response.status_code != 200:
                        return None
                    data = response.json()
                    # data is a list of commits, latest first
                    if isinstance(data, list) and len(data) > 0 and "id" in data[0]:
                        return data[0]["id"][:7]
                except Exception:
                    return None

        return None
