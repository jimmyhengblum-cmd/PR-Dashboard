"""
Configuration centralisée — charge les variables d'environnement et expose
les réglages de l'application.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class GitHubConfig:
    token: str = ""
    repos: list[str] = field(default_factory=list)

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.repos)


@dataclass
class AzureDevOpsConfig:
    token: str = ""
    organization: str = ""
    projects: list[str] = field(default_factory=list)  # "project/repo"

    @property
    def is_configured(self) -> bool:
        return bool(self.token and self.organization and self.projects)


@dataclass
class AppConfig:
    github: GitHubConfig
    azure: AzureDevOpsConfig
    current_user: str = ""
    auto_refresh_minutes: int = 5


def load_config() -> AppConfig:
    """Charge la configuration depuis les variables d'environnement."""

    gh_repos_raw = os.getenv("GITHUB_REPOS", "")
    gh_repos = [r.strip() for r in gh_repos_raw.split(",") if r.strip()]

    az_projects_raw = os.getenv("AZURE_DEVOPS_PROJECTS", "")
    az_projects = [p.strip() for p in az_projects_raw.split(",") if p.strip()]

    return AppConfig(
        github=GitHubConfig(
            token=os.getenv("GITHUB_TOKEN", ""),
            repos=gh_repos,
        ),
        azure=AzureDevOpsConfig(
            token=os.getenv("AZURE_DEVOPS_TOKEN", ""),
            organization=os.getenv("AZURE_DEVOPS_ORG", ""),
            projects=az_projects,
        ),
        current_user=os.getenv("CURRENT_USER", ""),
        auto_refresh_minutes=int(os.getenv("AUTO_REFRESH_MINUTES", "5")),
    )
