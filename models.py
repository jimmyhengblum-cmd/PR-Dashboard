"""
Modèles de données unifiés — structure commune pour les PR
quel que soit leur origine (GitHub / Azure DevOps).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── Enums ───────────────────────────────────────────────────────────────────

class Source(str, Enum):
    GITHUB = "GitHub"
    AZURE_DEVOPS = "Azure DevOps"


class TechnicalStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    ABANDONED = "abandoned"


class BusinessStatus(str, Enum):
    APPROVED = "Approved ✅"
    WAITING_REVIEW = "Waiting for review 👀"
    CHANGES_REQUESTED = "Changes requested 🔧"
    READY_TO_MERGE = "Ready to merge 🚀"
    BLOCKED = "Blocked (unresolved) ⛔"
    AT_RISK = "At risk ⚠️"
    COMPLETED = "Completed 🎉"
    ABANDONED = "Abandoned ❌"


class ReviewState(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    PENDING = "pending"
    COMMENTED = "commented"


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class Reviewer:
    username: str
    state: ReviewState


@dataclass
class UnifiedPR:
    """Structure unifiée représentant une Pull Request."""

    # Identité
    id: str                          # identifiant unique (source:repo:number)
    title: str
    source: Source
    repository: str
    author: str
    url: str

    # Temporel
    created_at: datetime
    updated_at: datetime | None = None

    # Statut technique
    technical_status: TechnicalStatus = TechnicalStatus.OPEN
    is_draft: bool = False

    # Reviews
    reviewers: list[Reviewer] = field(default_factory=list)
    has_unresolved_comments: bool = False

    # Taille
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0

    # Métier (calculé)
    business_status: BusinessStatus = BusinessStatus.WAITING_REVIEW
    priority_score: float = 0.0
    is_at_risk: bool = False
    risk_reasons: list[str] = field(default_factory=list)

    # Overrides manuels
    manual_status: str | None = None
    manual_priority: int | None = None
    manual_tags: list[str] = field(default_factory=list)
    manual_comment: str = ""

    # ── Propriétés calculées ────────────────────────────────────────────

    @property
    def age_days(self) -> float:
        delta = datetime.utcnow() - self.created_at
        return delta.total_seconds() / 86400

    @property
    def total_changes(self) -> int:
        return self.additions + self.deletions

    @property
    def review_count(self) -> int:
        return len(self.reviewers)

    @property
    def approved_count(self) -> int:
        return sum(1 for r in self.reviewers if r.state == ReviewState.APPROVED)

    @property
    def has_changes_requested(self) -> bool:
        return any(r.state == ReviewState.CHANGES_REQUESTED for r in self.reviewers)

    @property
    def display_status(self) -> str:
        """Retourne le statut override s'il existe, sinon le business status."""
        return self.manual_status or self.business_status.value

    @property
    def display_priority(self) -> int:
        """Retourne la priorité override ou le score calculé."""
        return self.manual_priority if self.manual_priority is not None else int(self.priority_score)

    def to_dict(self) -> dict:
        """Sérialise pour affichage dans un DataFrame."""
        return {
            "source": self.source.value,
            "repository": self.repository,
            "title": self.title,
            "author": self.author,
            "status": self.display_status,
            "priority": self.display_priority,
            "age_days": round(self.age_days, 1),
            "reviewers": self.review_count,
            "approved": self.approved_count,
            "changes": self.total_changes,
            "at_risk": "⚠️" if self.is_at_risk else "",
            "tags": ", ".join(self.manual_tags) if self.manual_tags else "",
            "url": self.url,
            "id": self.id,
        }
