"""
PR Dashboard — MVP
Tableau éditable par repo · colonnes emoji par membre + notes.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from base64 import b64encode
from dotenv import load_dotenv

# ── Config ──────────────────────────────────────────────────────────────────

load_dotenv()

st.set_page_config(
    page_title="PR Dashboard",
    page_icon="🔀",
    layout="wide",
    initial_sidebar_state="collapsed",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GH_REPOS = [r.strip() for r in os.getenv("GITHUB_REPOS", "").split(",") if r.strip()]
AZ_TOKEN = os.getenv("AZURE_DEVOPS_TOKEN", "")
AZ_ORG = os.getenv("AZURE_DEVOPS_ORG", "")
AZ_PROJECTS = [p.strip() for p in os.getenv("AZURE_DEVOPS_PROJECTS", "").split(",") if p.strip()]
HAS_TOKENS = bool(GH_TOKEN or AZ_TOKEN)

TEAM = ["CRI", "PCH", "JHE", "VTO", "SRI", "GPI", "SPL"]
EMOJI_OPTIONS = ["—", "👀", "✅", "⚠️", "🔧", "❌", "🚀"]
STORE_FILE = Path("user_marks.json")
EDITABLE_COLS = TEAM + ["Note"]


# ── Stockage local ──────────────────────────────────────────────────────────


def load_store() -> dict:
    if STORE_FILE.exists():
        try:
            return json.loads(STORE_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, TypeError):
            pass
    return {"marks": {}, "comments": {}}


def save_store(marks: dict, comments: dict) -> None:
    STORE_FILE.write_text(
        json.dumps({"marks": marks, "comments": comments}, indent=2, ensure_ascii=False),
        "utf-8",
    )


# ── API GitHub ──────────────────────────────────────────────────────────────


def fetch_github_prs(token: str, repo: str) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    params = {"state": "open", "per_page": 50, "sort": "updated", "direction": "desc"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("GitHub %s: %s", repo, e)
        return []
    prs = []
    for pr in resp.json():
        reviews = _fetch_gh_reviews(token, repo, pr["number"])
        status = _summarize_reviews(reviews)
        prs.append({
            "source": "GitHub", "repo": repo, "title": pr["title"],
            "author": pr.get("user", {}).get("login", "?"),
            "created": pr.get("created_at", "")[:10],
            "status": status,
            "updated": pr.get("updated_at", "")[:10],
            "comments": "💬" if pr.get("comments", 0) > 0 or pr.get("review_comments", 0) > 0 else "",
            "url": pr["html_url"],
            "id": f"gh:{repo}:{pr['number']}",
            "is_draft": pr.get("draft", False),
        })
    return prs


def _fetch_gh_reviews(token: str, repo: str, pr_number: int) -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return []


def _summarize_reviews(reviews: list[dict]) -> str:
    if not reviews:
        return "⏳ Pending"
    latest = {}
    for r in reviews:
        latest[r.get("user", {}).get("login", "?")] = r.get("state", "PENDING")
    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "🔧 Changes req."
    if states == {"APPROVED"}:
        return "✅ Approved"
    if "APPROVED" in states:
        return "✅⏳ Partial"
    if states <= {"COMMENTED", "PENDING", "DISMISSED"}:
        return "👀 In review"
    return "⏳ Pending"


# ── API Azure DevOps ────────────────────────────────────────────────────────


def fetch_azure_prs(token: str, org: str, project: str, repo: str) -> list[dict]:
    b64 = b64encode(f":{token}".encode()).decode()
    headers = {"Authorization": f"Basic {b64}", "Content-Type": "application/json"}
    url = f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}/pullrequests"
    params = {"searchCriteria.status": "active", "$top": 50, "api-version": "7.1-preview.1"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Azure %s/%s: %s", project, repo, e)
        return []
    prs = []
    for pr in resp.json().get("value", []):
        pr_id = pr["pullRequestId"]
        repo_name = pr.get("repository", {}).get("name", repo)
        status = _az_review_status(pr.get("reviewers", []))
        has_threads = _az_active_threads(token, org, project, repo_name, pr_id)
        prs.append({
            "source": "Azure DevOps", "repo": f"{project}/{repo_name}",
            "title": pr.get("title", ""),
            "author": pr.get("createdBy", {}).get("displayName", "?"),
            "created": (pr.get("creationDate") or "")[:10],
            "status": status,
            "updated": (pr.get("closedDate") or pr.get("creationDate", ""))[:10],
            "comments": "💬" if has_threads else "",
            "url": f"https://dev.azure.com/{org}/{project}/_git/{repo_name}/pullrequest/{pr_id}",
            "id": f"az:{project}/{repo_name}:{pr_id}",
            "is_draft": pr.get("isDraft", False),
        })
    return prs


def _az_review_status(reviewers: list[dict]) -> str:
    if not reviewers:
        return "⏳ Pending"
    votes = [r.get("vote", 0) for r in reviewers]
    if any(v <= -5 for v in votes):
        return "🔧 Changes req."
    if all(v >= 5 for v in votes):
        return "✅ Approved"
    if any(v >= 5 for v in votes):
        return "✅⏳ Partial"
    return "⏳ Pending"


def _az_active_threads(token: str, org: str, project: str, repo: str, pr_id: int) -> bool:
    b64 = b64encode(f":{token}".encode()).decode()
    url = f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}/pullRequests/{pr_id}/threads"
    try:
        resp = requests.get(url, headers={"Authorization": f"Basic {b64}"}, params={"api-version": "7.1-preview.1"}, timeout=15)
        resp.raise_for_status()
        return any((t.get("status") or "").lower() == "active" for t in resp.json().get("value", []))
    except requests.RequestException:
        return False


# ── Démo ────────────────────────────────────────────────────────────────────


def demo_data() -> list[dict]:
    from datetime import timedelta
    n = datetime.utcnow()
    d = timedelta
    return [
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "feat: Add new asset upload workflow", "author": "CRI", "created": (n-d(days=2)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(hours=3)).strftime("%Y-%m-%d"), "comments": "", "url": "https://github.com/myorg/digital-asset/pull/42", "id": "demo:digital-asset:42", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "fix: CORS issue on asset download", "author": "PCH", "created": (n-d(days=5)).strftime("%Y-%m-%d"), "status": "🔧 Changes req.", "updated": (n-d(days=1)).strftime("%Y-%m-%d"), "comments": "💬", "url": "https://github.com/myorg/digital-asset/pull/41", "id": "demo:digital-asset:41", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "refactor: Migrate storage to S3", "author": "JHE", "created": (n-d(days=1)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=6)).strftime("%Y-%m-%d"), "comments": "", "url": "https://github.com/myorg/digital-asset/pull/43", "id": "demo:digital-asset:43", "is_draft": True},
        {"source": "Azure DevOps", "repo": "Platform/auth-service", "title": "feat: SSO Azure AD integration", "author": "CRI", "created": (n-d(days=3)).strftime("%Y-%m-%d"), "status": "✅⏳ Partial", "updated": (n-d(hours=12)).strftime("%Y-%m-%d"), "comments": "💬", "url": "https://dev.azure.com/myorg/Platform/_git/auth-service/pullrequest/201", "id": "demo:auth-service:201", "is_draft": False},
        {"source": "Azure DevOps", "repo": "Platform/auth-service", "title": "fix: Token refresh race condition", "author": "PCH", "created": (n-d(days=7)).strftime("%Y-%m-%d"), "status": "👀 In review", "updated": (n-d(days=2)).strftime("%Y-%m-%d"), "comments": "💬", "url": "https://dev.azure.com/myorg/Platform/_git/auth-service/pullrequest/200", "id": "demo:auth-service:200", "is_draft": False},
        {"source": "Azure DevOps", "repo": "Platform/notification-svc", "title": "feat: Slack webhook notifications", "author": "JHE", "created": (n-d(days=1)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=2)).strftime("%Y-%m-%d"), "comments": "", "url": "https://dev.azure.com/myorg/Platform/_git/notification-svc/pullrequest/55", "id": "demo:notification-svc:55", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/data-pipeline", "title": "feat: Add dbt models for analytics", "author": "VTO", "created": (n-d(days=4)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(hours=8)).strftime("%Y-%m-%d"), "comments": "", "url": "https://github.com/myorg/data-pipeline/pull/78", "id": "demo:data-pipeline:78", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/data-pipeline", "title": "fix: Handle null values in ETL", "author": "SRI", "created": (n-d(hours=8)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=1)).strftime("%Y-%m-%d"), "comments": "", "url": "https://github.com/myorg/data-pipeline/pull/79", "id": "demo:data-pipeline:79", "is_draft": False},
    ]


# ── Helpers ─────────────────────────────────────────────────────────────────


def _clean(val) -> str:
    """Nettoie une valeur qui peut être None, NaN, ou string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def build_repo_df(repo_prs: list[dict], marks: dict, comments: dict) -> pd.DataFrame:
    rows = []
    for pr in sorted(repo_prs, key=lambda p: p["created"], reverse=True):
        draft = " [DRAFT]" if pr["is_draft"] else ""
        row = {
            "Auteur": pr["author"],
            "Titre": pr["title"] + draft,
            "Créée": pr["created"][5:],
            "Modif.": pr["updated"][5:],
            "Statut": pr["status"],
            "💬": pr["comments"],
            "Lien": pr["url"],
            "_id": pr["id"],
        }
        for m in TEAM:
            row[m] = marks.get(f"{pr['id']}:{m}", "—")
        row["Note"] = comments.get(f"cmt:{pr['id']}", "")
        rows.append(row)
    return pd.DataFrame(rows)


def extract_and_save(edited_df: pd.DataFrame, marks: dict, comments: dict) -> None:
    """Extrait les édits du DataFrame et sauvegarde sur disque + session_state."""
    for _, row in edited_df.iterrows():
        pr_id = row["_id"]
        for m in TEAM:
            val = _clean(row.get(m, ""))
            marks[f"{pr_id}:{m}"] = val if val else "—"
        comments[f"cmt:{pr_id}"] = _clean(row.get("Note", ""))

    # Persiste dans session_state (source de vérité pour le run courant)
    st.session_state["_marks"] = marks
    st.session_state["_comments"] = comments

    # Persiste sur disque (survit aux relances de l'app)
    save_store(marks, comments)


# ── CSS ─────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .block-container { padding-top: 0.8rem; padding-bottom: 1rem; }
    section[data-testid="stSidebar"] { display: none !important; }
    button[data-testid="stSidebarCollapsedControl"] { display: none !important; }
</style>
""", unsafe_allow_html=True)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    # ── Header ──────────────────────────────────────────────────────────
    st.markdown("## 🔀 PR Dashboard")
    left, right = st.columns([5, 1])
    left.caption("Suivi centralisé des Pull Requests · GitHub & Azure DevOps")
    refresh = right.button("🔄 Rafraîchir", use_container_width=True)

    # ── Source de vérité : session_state > fichier ──────────────────────
    if "_marks" not in st.session_state or "_comments" not in st.session_state:
        store = load_store()
        st.session_state["_marks"] = store.get("marks", {})
        st.session_state["_comments"] = store.get("comments", {})

    marks: dict = st.session_state["_marks"]
    comments: dict = st.session_state["_comments"]

    # ── Fetch PR data ───────────────────────────────────────────────────
    if "prs" not in st.session_state:
        st.session_state.prs = []

    use_demo = not HAS_TOKENS

    if use_demo:
        if not st.session_state.prs:
            st.session_state.prs = demo_data()
    else:
        first_load = not st.session_state.prs
        if refresh or first_load:
            all_prs = []
            if GH_TOKEN:
                for repo in GH_REPOS:
                    with st.spinner(f"GitHub: {repo}..."):
                        all_prs.extend(fetch_github_prs(GH_TOKEN, repo))
            if AZ_TOKEN and AZ_ORG:
                for proj_repo in AZ_PROJECTS:
                    parts = proj_repo.split("/")
                    if len(parts) == 2:
                        with st.spinner(f"Azure: {proj_repo}..."):
                            all_prs.extend(fetch_azure_prs(AZ_TOKEN, AZ_ORG, parts[0], parts[1]))
            st.session_state.prs = all_prs

    prs = st.session_state.prs
    if not prs:
        st.info("Aucune PR trouvée. Vérifie ta config .env puis clique 🔄 Rafraîchir.")
        return

    # ── Grouper par repo ────────────────────────────────────────────────
    repos: dict[str, list[dict]] = {}
    for pr in prs:
        repos.setdefault(pr["repo"], []).append(pr)

    # ── Config colonnes ─────────────────────────────────────────────────
    col_config = {
        "Auteur": st.column_config.TextColumn("Auteur", width="small", disabled=True),
        "Titre": st.column_config.TextColumn("Titre", width="large", disabled=True),
        "Créée": st.column_config.TextColumn("Créée", width="small", disabled=True),
        "Modif.": st.column_config.TextColumn("Modif.", width="small", disabled=True),
        "Statut": st.column_config.TextColumn("Statut", width="medium", disabled=True),
        "💬": st.column_config.TextColumn("💬", width="small", disabled=True),
        "Lien": st.column_config.LinkColumn("↗", width="small", display_text="↗"),
        "Note": st.column_config.TextColumn("Note 📝", width="medium"),
        "_id": None,
    }
    for m in TEAM:
        col_config[m] = st.column_config.SelectboxColumn(
            m, options=EMOJI_OPTIONS, width="small", default="—",
        )

    display_cols = ["Auteur", "Titre", "Créée", "Modif.", "Statut", "💬", "Lien"] + TEAM + ["Note"]

    # ── Affichage par repo ──────────────────────────────────────────────
    for repo_name, repo_prs in sorted(repos.items()):
        count = len(repo_prs)
        source = repo_prs[0]["source"]
        icon = "🐙" if source == "GitHub" else "🔷"

        with st.expander(f"{icon} **{repo_name}** · {count} PR{'s' if count > 1 else ''}", expanded=True):
            df = build_repo_df(repo_prs, marks, comments)

            edited = st.data_editor(
                df,
                column_config=col_config,
                column_order=display_cols,
                hide_index=True,
                use_container_width=True,
                key=f"ed_{repo_name}",
                num_rows="fixed",
            )

            # Sauvegarder à chaque run (pas de rerun !)
            # data_editor retourne toujours le df courant, donc on
            # extrait et persiste systématiquement — pas de comparaison.
            extract_and_save(edited, marks, comments)

    # ── Footer ──────────────────────────────────────────────────────────
    mode = "🎭 Démo" if use_demo else "🔴 Live"
    st.caption(f"{len(prs)} PR · {len(repos)} repos · {mode} · Équipe : {' · '.join(TEAM)}")


if __name__ == "__main__":
    main()
