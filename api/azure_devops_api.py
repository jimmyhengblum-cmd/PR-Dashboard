"""
Client pour l'API Azure DevOps — récupère les Pull Requests et leurs reviews.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime

import requests

from config import AzureDevOpsConfig
from models import (
    Reviewer,
    ReviewState,
    Source,
    TechnicalStatus,
    UnifiedPR,
)

logger = logging.getLogger(__name__)

API_VERSION = "7.1-preview.1"


def _headers(token: str) -> dict:
    # Azure DevOps utilise Basic auth avec un PAT (user vide)
    b64 = base64.b64encode(f":{token}".encode()).decode()
    return {
        "Authorization": f"Basic {b64}",
        "Content-Type": "application/json",
    }


def _parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.utcnow()
    # Azure DevOps renvoie des dates ISO 8601
    raw = raw.rstrip("Z").split(".")[0]  # truncate millisecondes
    return datetime.fromisoformat(raw)


def _map_vote_to_review_state(vote: int) -> ReviewState:
    """
    Azure DevOps utilise un système de votes numériques:
      10  = approved
       5  = approved with suggestions
       0  = no vote
      -5  = waiting for author
     -10  = rejected
    """
    if vote >= 10:
        return ReviewState.APPROVED
    elif vote == 5:
        return ReviewState.APPROVED
    elif vote == 0:
        return ReviewState.PENDING
    elif vote == -5:
        return ReviewState.CHANGES_REQUESTED
    elif vote <= -10:
        return ReviewState.CHANGES_REQUESTED
    return ReviewState.PENDING


def _map_status(status: str, merge_status: str | None = None) -> TechnicalStatus:
    """Mappe le statut Azure DevOps vers notre statut technique."""
    status_lower = (status or "").lower()
    if status_lower == "completed":
        return TechnicalStatus.MERGED
    elif status_lower == "abandoned":
        return TechnicalStatus.ABANDONED
    elif status_lower == "active":
        return TechnicalStatus.OPEN
    return TechnicalStatus.OPEN


def _has_unresolved_threads(
    token: str, org: str, project: str, repo_id: str, pr_id: int
) -> bool:
    """Vérifie s'il y a des threads non résolus sur la PR."""
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/git/"
        f"repositories/{repo_id}/pullRequests/{pr_id}/threads"
    )
    try:
        resp = requests.get(
            url, headers=_headers(token),
            params={"api-version": API_VERSION},
            timeout=15,
        )
        resp.raise_for_status()
        threads = resp.json().get("value", [])
        for thread in threads:
            status = (thread.get("status") or "").lower()
            if status == "active":
                return True
    except requests.RequestException as exc:
        logger.warning("Échec threads Azure PR %d: %s", pr_id, exc)
    return False


def fetch_azure_devops_prs(config: AzureDevOpsConfig) -> list[UnifiedPR]:
    """Récupère toutes les PR des projets/repos configurés."""
    if not config.is_configured:
        logger.info("Azure DevOps non configuré — skip")
        return []

    all_prs: list[UnifiedPR] = []

    for project_repo in config.projects:
        parts = project_repo.split("/")
        if len(parts) != 2:
            logger.warning("Format invalide (attendu project/repo): %s", project_repo)
            continue

        project, repo = parts
        org = config.organization

        # Récupérer les PR (toutes les actives + récentes complétées)
        for status_filter in ["active", "completed", "abandoned"]:
            url = (
                f"https://dev.azure.com/{org}/{project}/_apis/git/"
                f"repositories/{repo}/pullrequests"
            )
            params = {
                "searchCriteria.status": status_filter,
                "$top": 50,
                "api-version": API_VERSION,
            }

            try:
                resp = requests.get(
                    url, headers=_headers(config.token), params=params, timeout=20
                )
                resp.raise_for_status()
                pulls = resp.json().get("value", [])
            except requests.RequestException as exc:
                logger.error("Erreur API Azure pour %s (%s): %s", project_repo, status_filter, exc)
                continue

            for pr_data in pulls:
                pr_id = pr_data["pullRequestId"]
                repo_name = pr_data.get("repository", {}).get("name", repo)
                repo_id = pr_data.get("repository", {}).get("id", "")
                full_repo = f"{project}/{repo_name}"

                tech_status = _map_status(pr_data.get("status", "active"))

                # Reviewers depuis les votes
                reviewers: list[Reviewer] = []
                for reviewer_data in pr_data.get("reviewers", []):
                    username = reviewer_data.get("uniqueName", reviewer_data.get("displayName", "unknown"))
                    vote = reviewer_data.get("vote", 0)
                    reviewers.append(Reviewer(
                        username=username,
                        state=_map_vote_to_review_state(vote),
                    ))

                # Threads non résolus (seulement PR actives)
                unresolved = False
                if tech_status == TechnicalStatus.OPEN and repo_id:
                    unresolved = _has_unresolved_threads(
                        config.token, org, project, repo_id, pr_id
                    )

                # Taille : Azure DevOps ne fournit pas additions/deletions dans le listing
                # Il faudrait un appel supplémentaire par PR — on met 0 par défaut
                pr = UnifiedPR(
                    id=f"azure:{full_repo}:{pr_id}",
                    title=pr_data.get("title", ""),
                    source=Source.AZURE_DEVOPS,
                    repository=full_repo,
                    author=pr_data.get("createdBy", {}).get("uniqueName", "unknown"),
                    url=(
                        f"https://dev.azure.com/{org}/{project}/_git/{repo_name}"
                        f"/pullrequest/{pr_id}"
                    ),
                    created_at=_parse_datetime(pr_data.get("creationDate")),
                    updated_at=_parse_datetime(pr_data.get("closedDate") or pr_data.get("creationDate")),
                    technical_status=tech_status,
                    is_draft=pr_data.get("isDraft", False),
                    reviewers=reviewers,
                    has_unresolved_comments=unresolved,
                )
                all_prs.append(pr)

    logger.info("Azure DevOps: %d PR récupérées", len(all_prs))
    return all_prs
