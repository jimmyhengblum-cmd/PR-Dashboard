"""
Moteur de priorité et détection de risques.

Score de priorité = ancienneté + taille + blocage + activité reviews
Plus le score est élevé, plus la PR nécessite de l'attention.
"""

from __future__ import annotations

from models import BusinessStatus, TechnicalStatus, UnifiedPR


# ── Seuils configurables ────────────────────────────────────────────────────

AGE_RISK_DAYS = 3          # PR ouverte depuis > X jours → at risk
AGE_CRITICAL_DAYS = 7      # PR ouverte depuis > X jours → critique
LARGE_PR_THRESHOLD = 500   # > X lignes modifiées → PR "grosse"
HUGE_PR_THRESHOLD = 1500   # > X lignes modifiées → PR "énorme"


def compute_priority_score(pr: UnifiedPR) -> float:
    """
    Calcule un score de priorité (0-100+).

    Composantes :
    - Ancienneté        : 0-40 pts (linéaire, plafonné à 10 jours)
    - Taille            : 0-20 pts
    - Blocage           : 0-25 pts
    - Manque de reviews : 0-15 pts
    """
    score = 0.0

    # ── Ancienneté (max 40 pts) ─────────────────────────────────────────
    age = pr.age_days
    score += min(age * 4, 40)

    # ── Taille de la PR (max 20 pts) ────────────────────────────────────
    changes = pr.total_changes
    if changes > HUGE_PR_THRESHOLD:
        score += 20
    elif changes > LARGE_PR_THRESHOLD:
        score += 12
    elif changes > 200:
        score += 6
    elif changes > 50:
        score += 2

    # ── Blocage (max 25 pts) ────────────────────────────────────────────
    if pr.has_unresolved_comments:
        score += 15
    if pr.has_changes_requested:
        score += 10

    # ── Manque de reviews (max 15 pts) ──────────────────────────────────
    if pr.review_count == 0 and pr.technical_status == TechnicalStatus.OPEN:
        score += 15
    elif pr.approved_count == 0 and pr.review_count > 0:
        score += 8

    return round(score, 1)


def detect_risks(pr: UnifiedPR) -> tuple[bool, list[str]]:
    """
    Détecte si une PR est "à risque" et retourne les raisons.

    Une PR est à risque si :
    - ouverte depuis > 3 jours sans approbation
    - pas de reviewers assignés
    - changes requested depuis > 1 jour
    - très grosse PR sans review
    """
    if pr.technical_status != TechnicalStatus.OPEN:
        return False, []

    reasons: list[str] = []

    # PR âgée sans approbation
    if pr.age_days > AGE_RISK_DAYS and pr.approved_count == 0:
        reasons.append(f"Ouverte depuis {pr.age_days:.0f} jours sans approbation")

    if pr.age_days > AGE_CRITICAL_DAYS:
        reasons.append(f"PR critique : {pr.age_days:.0f} jours d'ancienneté")

    # Pas de reviewers
    if pr.review_count == 0:
        reasons.append("Aucun reviewer assigné")

    # Changes requested non traitées
    if pr.has_changes_requested and pr.age_days > 1:
        reasons.append("Changes requested en attente")

    # Grosse PR sans review
    if pr.total_changes > LARGE_PR_THRESHOLD and pr.approved_count == 0:
        reasons.append(f"Grosse PR ({pr.total_changes} lignes) sans approbation")

    # Commentaires non résolus depuis longtemps
    if pr.has_unresolved_comments and pr.age_days > 2:
        reasons.append("Commentaires non résolus depuis > 2 jours")

    is_at_risk = len(reasons) > 0
    return is_at_risk, reasons


def apply_priority_and_risks(prs: list[UnifiedPR]) -> list[UnifiedPR]:
    """Calcule le score de priorité et les risques pour toutes les PR."""
    for pr in prs:
        pr.priority_score = compute_priority_score(pr)
        pr.is_at_risk, pr.risk_reasons = detect_risks(pr)

        # Override le statut métier si at_risk et actuellement en attente
        if pr.is_at_risk and pr.business_status == BusinessStatus.WAITING_REVIEW:
            pr.business_status = BusinessStatus.AT_RISK

    return prs


def suggest_tags(pr: UnifiedPR) -> list[str]:
    """Suggère des tags automatiques basés sur les caractéristiques de la PR."""
    tags: list[str] = []

    # Taille
    if pr.total_changes < 50:
        tags.append("#quick-win")
    elif pr.total_changes > HUGE_PR_THRESHOLD:
        tags.append("#large-pr")

    # Criticité
    if pr.age_days > AGE_CRITICAL_DAYS:
        tags.append("#critical")
    elif pr.is_at_risk:
        tags.append("#at-risk")

    # Ready
    if pr.business_status == BusinessStatus.READY_TO_MERGE:
        tags.append("#ready")

    # Titre heuristiques
    title_lower = pr.title.lower()
    if any(w in title_lower for w in ("fix", "bug", "hotfix", "patch")):
        tags.append("#bugfix")
    if any(w in title_lower for w in ("feat", "feature", "add", "new")):
        tags.append("#feature")
    if any(w in title_lower for w in ("refactor", "clean", "tech debt")):
        tags.append("#refactor")
    if any(w in title_lower for w in ("doc", "readme", "documentation")):
        tags.append("#docs")
    if any(w in title_lower for w in ("ci", "cd", "pipeline", "deploy")):
        tags.append("#devops")
    if any(w in title_lower for w in ("front", "ui", "css", "react", "vue")):
        tags.append("#frontend")
    if any(w in title_lower for w in ("api", "backend", "server", "db")):
        tags.append("#backend")
    if any(w in title_lower for w in ("test", "spec", "e2e")):
        tags.append("#tests")

    return tags
