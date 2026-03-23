"""
Stockage local des overrides manuels (JSON).
Permet de sauvegarder les modifications manuelles de statut,
priorité, tags et commentaires par PR.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

logger = logging.getLogger(__name__)

STORE_PATH = Path("local_overrides.json")


@dataclass
class PROverride:
    """Override manuel pour une PR."""
    pr_id: str
    status: str | None = None
    priority: int | None = None
    tags: list[str] = field(default_factory=list)
    comment: str = ""


class LocalStore:
    """Gestionnaire de stockage local pour les overrides manuels."""

    def __init__(self, path: Path = STORE_PATH):
        self.path = path
        self._data: dict[str, PROverride] = {}
        self._load()

    def _load(self) -> None:
        """Charge les overrides depuis le fichier JSON."""
        if not self.path.exists():
            self._data = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = {
                k: PROverride(**v) for k, v in raw.items()
            }
            logger.info("Chargé %d overrides depuis %s", len(self._data), self.path)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Erreur lecture store: %s — reset", exc)
            self._data = {}

    def _save(self) -> None:
        """Sauvegarde les overrides dans le fichier JSON."""
        raw = {k: asdict(v) for k, v in self._data.items()}
        self.path.write_text(
            json.dumps(raw, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, pr_id: str) -> PROverride | None:
        """Récupère l'override pour une PR donnée."""
        return self._data.get(pr_id)

    def set(self, override: PROverride) -> None:
        """Crée ou met à jour un override."""
        self._data[override.pr_id] = override
        self._save()

    def update_status(self, pr_id: str, status: str | None) -> None:
        existing = self._data.get(pr_id, PROverride(pr_id=pr_id))
        existing.status = status
        self._data[pr_id] = existing
        self._save()

    def update_priority(self, pr_id: str, priority: int | None) -> None:
        existing = self._data.get(pr_id, PROverride(pr_id=pr_id))
        existing.priority = priority
        self._data[pr_id] = existing
        self._save()

    def update_tags(self, pr_id: str, tags: list[str]) -> None:
        existing = self._data.get(pr_id, PROverride(pr_id=pr_id))
        existing.tags = tags
        self._data[pr_id] = existing
        self._save()

    def update_comment(self, pr_id: str, comment: str) -> None:
        existing = self._data.get(pr_id, PROverride(pr_id=pr_id))
        existing.comment = comment
        self._data[pr_id] = existing
        self._save()

    def delete(self, pr_id: str) -> None:
        """Supprime l'override d'une PR."""
        if pr_id in self._data:
            del self._data[pr_id]
            self._save()

    def clear_all(self) -> None:
        """Supprime tous les overrides."""
        self._data = {}
        self._save()

    @property
    def all_overrides(self) -> dict[str, PROverride]:
        return dict(self._data)
