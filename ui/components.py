"""
Composants UI Streamlit — affichage du dashboard, filtres, vues spécialisées.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from models import BusinessStatus, TechnicalStatus, UnifiedPR
from storage.local_store import LocalStore


# ── Couleurs par statut ─────────────────────────────────────────────────────

STATUS_COLORS: dict[str, str] = {
    BusinessStatus.APPROVED.value: "#22c55e",
    BusinessStatus.WAITING_REVIEW.value: "#f59e0b",
    BusinessStatus.CHANGES_REQUESTED.value: "#ef4444",
    BusinessStatus.READY_TO_MERGE.value: "#3b82f6",
    BusinessStatus.BLOCKED.value: "#dc2626",
    BusinessStatus.AT_RISK.value: "#f97316",
    BusinessStatus.COMPLETED.value: "#10b981",
    BusinessStatus.ABANDONED.value: "#6b7280",
}


# ── KPI Header ──────────────────────────────────────────────────────────────

def render_kpi_bar(prs: list[UnifiedPR]) -> None:
    """Affiche les KPI principaux en haut du dashboard."""
    open_prs = [p for p in prs if p.technical_status == TechnicalStatus.OPEN]
    at_risk = [p for p in open_prs if p.is_at_risk]
    ready = [p for p in open_prs if p.business_status == BusinessStatus.READY_TO_MERGE]
    blocked = [p for p in open_prs if p.business_status == BusinessStatus.BLOCKED]
    waiting = [p for p in open_prs if p.business_status == BusinessStatus.WAITING_REVIEW]

    cols = st.columns(6)
    cols[0].metric("📋 Total ouvertes", len(open_prs))
    cols[1].metric("👀 En attente review", len(waiting))
    cols[2].metric("🚀 Prêtes à merge", len(ready))
    cols[3].metric("🔧 Changes requested", sum(1 for p in open_prs if p.has_changes_requested))
    cols[4].metric("⛔ Bloquées", len(blocked))
    cols[5].metric("⚠️ À risque", len(at_risk))


# ── Filtres ─────────────────────────────────────────────────────────────────

def render_filters(prs: list[UnifiedPR]) -> list[UnifiedPR]:
    """Affiche les filtres dans la sidebar et retourne les PR filtrées."""

    st.sidebar.header("🔍 Filtres")

    # Sources
    sources = sorted(set(p.source.value for p in prs))
    selected_sources = st.sidebar.multiselect("Source", sources, default=sources)

    # Repos
    repos = sorted(set(p.repository for p in prs))
    selected_repos = st.sidebar.multiselect("Repository", repos, default=repos)

    # Auteurs
    authors = sorted(set(p.author for p in prs))
    selected_authors = st.sidebar.multiselect("Auteur", authors, default=authors)

    # Statuts
    statuses = sorted(set(p.display_status for p in prs))
    selected_statuses = st.sidebar.multiselect("Statut", statuses, default=statuses)

    # Risque only
    risk_only = st.sidebar.checkbox("⚠️ Seulement les PR à risque", value=False)

    # Open only
    open_only = st.sidebar.checkbox("📋 Seulement les PR ouvertes", value=True)

    # Apply filters
    filtered = prs
    filtered = [p for p in filtered if p.source.value in selected_sources]
    filtered = [p for p in filtered if p.repository in selected_repos]
    filtered = [p for p in filtered if p.author in selected_authors]
    filtered = [p for p in filtered if p.display_status in selected_statuses]
    if risk_only:
        filtered = [p for p in filtered if p.is_at_risk]
    if open_only:
        filtered = [p for p in filtered if p.technical_status == TechnicalStatus.OPEN]

    # Tri
    sort_by = st.sidebar.selectbox(
        "Trier par",
        ["Priorité (desc)", "Date création (récent)", "Date création (ancien)", "Âge (desc)"],
    )
    if sort_by == "Priorité (desc)":
        filtered.sort(key=lambda p: p.display_priority, reverse=True)
    elif sort_by == "Date création (récent)":
        filtered.sort(key=lambda p: p.created_at, reverse=True)
    elif sort_by == "Date création (ancien)":
        filtered.sort(key=lambda p: p.created_at)
    elif sort_by == "Âge (desc)":
        filtered.sort(key=lambda p: p.age_days, reverse=True)

    return filtered


# ── Tableau principal ───────────────────────────────────────────────────────

def render_pr_table(prs: list[UnifiedPR]) -> None:
    """Affiche le tableau principal des PR."""
    if not prs:
        st.info("Aucune PR à afficher avec les filtres actuels.")
        return

    rows = [pr.to_dict() for pr in prs]
    df = pd.DataFrame(rows)

    # Renommer colonnes
    df = df.rename(columns={
        "source": "Source",
        "repository": "Repo",
        "title": "Titre",
        "author": "Auteur",
        "status": "Statut",
        "priority": "Priorité",
        "age_days": "Âge (j)",
        "reviewers": "Reviewers",
        "approved": "Approbations",
        "changes": "Lignes",
        "at_risk": "Risque",
        "tags": "Tags",
        "url": "Lien",
    })

    # Affichage
    st.dataframe(
        df[["Source", "Repo", "Titre", "Auteur", "Statut", "Priorité",
            "Âge (j)", "Reviewers", "Approbations", "Lignes", "Risque", "Tags", "Lien"]],
        column_config={
            "Lien": st.column_config.LinkColumn("Lien", display_text="Ouvrir ↗"),
            "Priorité": st.column_config.ProgressColumn("Priorité", min_value=0, max_value=100),
        },
        hide_index=True,
        use_container_width=True,
        height=min(len(df) * 40 + 60, 600),
    )


# ── Vue actionnable (par utilisateur) ──────────────────────────────────────

def render_actionable_view(prs: list[UnifiedPR], current_user: str) -> None:
    """Vue 'Ce que TU dois faire' filtrée par l'utilisateur courant."""

    st.subheader(f"🎯 Actions pour {current_user or 'toi'}")

    open_prs = [p for p in prs if p.technical_status == TechnicalStatus.OPEN]

    # PR à review (où tu es reviewer pending)
    to_review = [
        p for p in open_prs
        if any(
            r.username.lower() == current_user.lower() and r.state.value == "pending"
            for r in p.reviewers
        )
    ]

    # PR bloquées que tu as créées
    my_blocked = [
        p for p in open_prs
        if p.author.lower() == current_user.lower()
        and p.business_status in (BusinessStatus.BLOCKED, BusinessStatus.CHANGES_REQUESTED)
    ]

    # PR prêtes à merge que tu as créées
    my_ready = [
        p for p in open_prs
        if p.author.lower() == current_user.lower()
        and p.business_status == BusinessStatus.READY_TO_MERGE
    ]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("#### 👀 À reviewer")
        if to_review:
            for pr in to_review:
                st.markdown(f"- [{pr.title}]({pr.url}) — *{pr.repository}*")
        else:
            st.caption("Rien à reviewer ✨")

    with col2:
        st.markdown("#### 🔧 Tes PR bloquées")
        if my_blocked:
            for pr in my_blocked:
                st.markdown(f"- [{pr.title}]({pr.url}) — {pr.display_status}")
        else:
            st.caption("Aucune PR bloquée 🎉")

    with col3:
        st.markdown("#### 🚀 Prêtes à merge")
        if my_ready:
            for pr in my_ready:
                st.markdown(f"- [{pr.title}]({pr.url}) — *{pr.repository}*")
        else:
            st.caption("Aucune PR prête")


# ── Mode Standup ────────────────────────────────────────────────────────────

def render_standup_view(prs: list[UnifiedPR]) -> None:
    """Vue Kanban simplifiée pour les daily standups."""

    st.subheader("📋 Mode Standup")

    open_prs = [p for p in prs if p.technical_status == TechnicalStatus.OPEN]

    categories = {
        "🆕 Nouvelles (< 1 jour)": [p for p in open_prs if p.age_days < 1],
        "👀 En review": [p for p in open_prs if p.business_status == BusinessStatus.WAITING_REVIEW],
        "🔧 Changes requested": [p for p in open_prs if p.has_changes_requested],
        "⛔ Bloquées": [p for p in open_prs if p.business_status == BusinessStatus.BLOCKED],
        "🚀 Ready to merge": [p for p in open_prs if p.business_status == BusinessStatus.READY_TO_MERGE],
    }

    # Récentes terminées
    done = [p for p in prs if p.technical_status == TechnicalStatus.MERGED and p.age_days < 2]
    categories["🎉 Mergées (< 48h)"] = done

    cols = st.columns(len(categories))
    for col, (label, items) in zip(cols, categories.items()):
        with col:
            st.markdown(f"**{label}** ({len(items)})")
            st.divider()
            for pr in items[:10]:
                st.markdown(
                    f"<div style='padding:6px 8px;margin-bottom:6px;border-radius:6px;"
                    f"background:rgba(255,255,255,0.05);border-left:3px solid "
                    f"{STATUS_COLORS.get(pr.display_status, '#888')}'>"
                    f"<small><b>{pr.title[:40]}{'…' if len(pr.title) > 40 else ''}</b><br>"
                    f"<span style='color:#aaa'>{pr.author} · {pr.repository}</span></small>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if not items:
                st.caption("—")


# ── Heatmap repos ──────────────────────────────────────────────────────────

def render_repo_heatmap(prs: list[UnifiedPR]) -> None:
    """Heatmap : nombre de PR ouvertes par repo."""

    st.subheader("🗺️ Charge par repository")

    open_prs = [p for p in prs if p.technical_status == TechnicalStatus.OPEN]
    repo_counts: dict[str, int] = {}
    for p in open_prs:
        repo_counts[p.repository] = repo_counts.get(p.repository, 0) + 1

    if not repo_counts:
        st.info("Aucune PR ouverte.")
        return

    df = pd.DataFrame(
        [{"Repository": k, "PR ouvertes": v} for k, v in sorted(repo_counts.items(), key=lambda x: -x[1])]
    )
    st.bar_chart(df.set_index("Repository"))


# ── Override manuel ─────────────────────────────────────────────────────────

def render_manual_override(prs: list[UnifiedPR], store: LocalStore) -> None:
    """Interface d'override manuel pour une PR sélectionnée."""

    st.subheader("✏️ Override manuel")

    if not prs:
        st.info("Aucune PR disponible.")
        return

    pr_options = {f"{p.repository} — {p.title[:60]}": p for p in prs}
    selected_label = st.selectbox("Sélectionner une PR", list(pr_options.keys()))

    if not selected_label:
        return

    pr = pr_options[selected_label]
    existing = store.get(pr.id)

    col1, col2 = st.columns(2)

    with col1:
        status_options = ["(auto)"] + [s.value for s in BusinessStatus]
        current_idx = 0
        if existing and existing.status:
            try:
                current_idx = status_options.index(existing.status)
            except ValueError:
                pass
        new_status = st.selectbox("Statut manuel", status_options, index=current_idx)

        new_priority = st.slider(
            "Priorité manuelle",
            0, 100,
            value=existing.priority if existing and existing.priority is not None else int(pr.priority_score),
        )

    with col2:
        default_tags = ", ".join(existing.tags) if existing else ", ".join(pr.manual_tags)
        tags_input = st.text_input("Tags (séparés par virgule)", value=default_tags)

        comment = st.text_area(
            "Commentaire",
            value=existing.comment if existing else "",
            height=100,
        )

    if st.button("💾 Sauvegarder l'override", type="primary"):
        from storage.local_store import PROverride

        override = PROverride(
            pr_id=pr.id,
            status=new_status if new_status != "(auto)" else None,
            priority=new_priority if new_priority != int(pr.priority_score) else None,
            tags=[t.strip() for t in tags_input.split(",") if t.strip()],
            comment=comment,
        )
        store.set(override)
        st.success(f"Override sauvegardé pour : {pr.title[:50]}")
        st.rerun()

    if existing and st.button("🗑️ Supprimer l'override"):
        store.delete(pr.id)
        st.success("Override supprimé.")
        st.rerun()


# ── Alertes ─────────────────────────────────────────────────────────────────

def render_alerts(prs: list[UnifiedPR]) -> None:
    """Affiche les alertes pour les PR à risque."""
    at_risk = [p for p in prs if p.is_at_risk and p.technical_status == TechnicalStatus.OPEN]

    if not at_risk:
        return

    with st.expander(f"🚨 {len(at_risk)} PR à risque", expanded=True):
        for pr in sorted(at_risk, key=lambda p: p.priority_score, reverse=True):
            reasons = " · ".join(pr.risk_reasons)
            st.warning(
                f"**[{pr.title[:50]}]({pr.url})** — {pr.repository}\n\n"
                f"Auteur : {pr.author} · Âge : {pr.age_days:.0f}j · Raisons : {reasons}"
            )
