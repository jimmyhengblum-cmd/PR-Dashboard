"""
Client pour l'API GitHub — récupère les Pull Requests et leurs reviews.
"""

from __future__ import annotations

import logging
from datetime import datetime

import requests

from config import GitHubConfig
from models import (
    Reviewer,
    ReviewState,
    Source,
    TechnicalStatus,
    UnifiedPR,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.utcnow()
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def _map_review_state(state: str) -> ReviewState:
    mapping = {
        "APPROVED": ReviewState.APPROVED,
        "CHANGES_REQUESTED": ReviewState.CHANGES_REQUESTED,
        "COMMENTED": ReviewState.COMMENTED,
        "PENDING": ReviewState.PENDING,
        "DISMISSED": ReviewState.PENDING,
    }
    return mapping.get(state.upper(), ReviewState.PENDING)


def _get_reviews(token: str, owner: str, repo: str, pr_number: int) -> list[Reviewer]:
    """Récupère les reviews d'une PR (dernier état par reviewer)."""
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Échec récupération reviews %s/%s#%d: %s", owner, repo, pr_number, exc)
        return []

    # Garder le dernier état par reviewer
    latest: dict[str, Reviewer] = {}
    for review in resp.json():
        user = review.get("user", {}).get("login", "unknown")
        state = _map_review_state(review.get("state", "PENDING"))
        latest[user] = Reviewer(username=user, state=state)

    return list(latest.values())


def _has_unresolved_comments(token: str, owner: str, repo: str, pr_number: int) -> bool:
    """Vérifie s'il reste des review threads non résolus (via review comments count heuristique)."""
    # L'API REST GitHub ne fournit pas directement les threads résolus
    # On utilise le nombre de review comments comme indicateur
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
    try:
        resp = requests.get(url, headers=_headers(token), timeout=15, params={"per_page": 1})
        resp.raise_for_status()
        # Heuristique : s'il y a des comments, on signale potentiellement non résolu
        # Pour une vérification exacte, il faudrait l'API GraphQL
        total = int(resp.headers.get("X-Total-Count", len(resp.json())))
        return total > 0
    except requests.RequestException:
        return False


def fetch_github_prs(config: GitHubConfig) -> list[UnifiedPR]:
    """Récupère toutes les PR ouvertes des repos configurés."""
    if not config.is_configured:
        logger.info("GitHub non configuré — skip")
        return []

    all_prs: list[UnifiedPR] = []

    for repo_full in config.repos:
        parts = repo_full.split("/")
        if len(parts) != 2:
            logger.warning("Format repo invalide (attendu owner/repo): %s", repo_full)
            continue

        owner, repo = parts
        url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
        params = {"state": "all", "per_page": 50, "sort": "updated", "direction": "desc"}

        try:
            resp = requests.get(url, headers=_headers(config.token), params=params, timeout=20)
            resp.raise_for_status()
            pulls = resp.json()
        except requests.RequestException as exc:
            logger.error("Erreur API GitHub pour %s: %s", repo_full, exc)
            continue

        for pr_data in pulls:
            pr_number = pr_data["number"]

            # Déterminer le statut technique
            if pr_data.get("merged_at"):
                tech_status = TechnicalStatus.MERGED
            elif pr_data["state"] == "closed":
                tech_status = TechnicalStatus.CLOSED
            else:
                tech_status = TechnicalStatus.OPEN

            # Récupérer les reviews seulement pour les PR ouvertes (perf)
            reviewers = []
            unresolved = False
            if tech_status == TechnicalStatus.OPEN:
                reviewers = _get_reviews(config.token, owner, repo, pr_number)
                unresolved = _has_unresolved_comments(config.token, owner, repo, pr_number)

            pr = UnifiedPR(
                id=f"github:{repo_full}:{pr_number}",
                title=pr_data["title"],
                source=Source.GITHUB,
                repository=repo_full,
                author=pr_data.get("user", {}).get("login", "unknown"),
                url=pr_data["html_url"],
                created_at=_parse_datetime(pr_data.get("created_at")),
                updated_at=_parse_datetime(pr_data.get("updated_at")),
                technical_status=tech_status,
                is_draft=pr_data.get("draft", False),
                reviewers=reviewers,
                has_unresolved_comments=unresolved,
                additions=pr_data.get("additions", 0),
                deletions=pr_data.get("deletions", 0),
                changed_files=pr_data.get("changed_files", 0),
            )
            all_prs.append(pr)

    logger.info("GitHub: %d PR récupérées", len(all_prs))
    return all_prs
