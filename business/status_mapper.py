"""
Mapper de statuts — transforme les données techniques en statuts métier
lisibles et actionnables.
"""

from __future__ import annotations

from models import BusinessStatus, TechnicalStatus, UnifiedPR


def compute_business_status(pr: UnifiedPR) -> BusinessStatus:
    """
    Détermine le statut métier d'une PR à partir de ses données techniques.

    Logique de priorité :
    1. PR terminées (merged / abandoned)
    2. PR bloquées (commentaires non résolus)
    3. Changes requested
    4. Ready to merge (approved + pas de blocage)
    5. Approved (mais pas encore prêt — ex: draft)
    6. Waiting for review (défaut)
    """

    # ── PR terminées ────────────────────────────────────────────────────
    if pr.technical_status == TechnicalStatus.MERGED:
        return BusinessStatus.COMPLETED

    if pr.technical_status in (TechnicalStatus.CLOSED, TechnicalStatus.ABANDONED):
        return BusinessStatus.ABANDONED

    # ── PR ouvertes : analyse des reviews ───────────────────────────────

    # Bloquée par des commentaires non résolus
    if pr.has_unresolved_comments:
        return BusinessStatus.BLOCKED

    # Changes requested par au moins un reviewer
    if pr.has_changes_requested:
        return BusinessStatus.CHANGES_REQUESTED

    # Approved par au moins un reviewer
    if pr.approved_count > 0:
        # Ready to merge : approved + pas draft + pas de blocage
        if not pr.is_draft and not pr.has_unresolved_comments:
            return BusinessStatus.READY_TO_MERGE
        return BusinessStatus.APPROVED

    # Aucun reviewer ou tous en pending
    return BusinessStatus.WAITING_REVIEW


def apply_business_status(pr: UnifiedPR) -> UnifiedPR:
    """Calcule et assigne le statut métier sur la PR (mutation in-place)."""
    pr.business_status = compute_business_status(pr)
    return pr


def apply_all(prs: list[UnifiedPR]) -> list[UnifiedPR]:
    """Applique le statut métier à une liste de PR."""
    return [apply_business_status(pr) for pr in prs]
