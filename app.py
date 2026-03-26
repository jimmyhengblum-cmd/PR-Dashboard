"""
PR Dashboard
Editable table per repo · SPL emoji column + notes.
"""

import json
import logging
import os
from datetime import datetime, timedelta
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
    page_icon="logo.svg",
    layout="wide",
    initial_sidebar_state="collapsed",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GH_TOKEN = os.getenv("GITHUB_TOKEN", "")
GH_REPOS = list(dict.fromkeys(r.strip() for r in os.getenv("GITHUB_REPOS", "").split(",") if r.strip()))
AZ_TOKEN = os.getenv("AZURE_DEVOPS_TOKEN", "")
AZ_ORG = os.getenv("AZURE_DEVOPS_ORG", "")
AZ_PROJECTS = list(dict.fromkeys(p.strip() for p in os.getenv("AZURE_DEVOPS_PROJECTS", "").split(",") if p.strip()))
HAS_TOKENS = bool(GH_TOKEN or AZ_TOKEN)

EMOJI_OPTIONS = ["—", "👀", "✅", "⚠️", "🔧", "❌", "🚀"]
STORE_FILE = Path("user_marks.json")
EDITABLE_COLS = ["SPL", "Note"]
PROJECTS_FILE = Path("projects.json")


# ── Project mapping ────────────────────────────────────────────────────────


def load_projects() -> dict:
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text("utf-8"))
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


# ── Local storage ──────────────────────────────────────────────────────────


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


# ── GitHub API ─────────────────────────────────────────────────────────────


def fetch_github_prs(token: str, repo: str, state: str = "open") -> list[dict]:
    url = f"https://api.github.com/repos/{repo}/pulls"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    params = {"state": state, "per_page": 50, "sort": "updated", "direction": "desc"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("GitHub %s: %s", repo, e)
        return []
    prs = []
    now = datetime.utcnow()
    for pr in resp.json():
        reviews = _fetch_gh_reviews(token, repo, pr["number"])
        status = _summarize_reviews(reviews)
        reviewer_names = _extract_gh_reviewers(reviews)
        created_str = pr.get("created_at", "")[:10]

        # For closed PRs, skip if older than 5 days
        if state == "closed":
            closed_at = pr.get("closed_at", "")
            if closed_at:
                try:
                    closed_date = datetime.strptime(closed_at[:10], "%Y-%m-%d")
                    if (now - closed_date).days > 5:
                        continue
                except ValueError:
                    pass

        # Calculate age in days
        age = 0
        if created_str:
            try:
                age = (now - datetime.strptime(created_str, "%Y-%m-%d")).days
            except ValueError:
                pass

        assignees = ", ".join(
            a.get("login", "?") for a in (pr.get("assignees") or [])
        )

        # Merge submitted reviewers with requested reviewers
        requested = [
            u.get("login", "") for u in (pr.get("requested_reviewers") or [])
            if u.get("login")
        ]
        all_reviewers = reviewer_names
        if requested:
            existing = set(all_reviewers.split(", ")) if all_reviewers else set()
            extra = [r for r in requested if r not in existing]
            if extra:
                all_reviewers = ", ".join(filter(None, [all_reviewers, ", ".join(extra)]))

        prs.append({
            "source": "GitHub", "repo": repo, "title": pr["title"],
            "author": pr.get("user", {}).get("login", "?"),
            "from_branch": pr.get("head", {}).get("ref", ""),
            "into_branch": pr.get("base", {}).get("ref", ""),
            "created": created_str,
            "updated": pr.get("updated_at", "")[:10],
            "age": age,
            "status": status,
            "assignee": assignees,
            "reviewed_by": all_reviewers,
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


def _extract_gh_reviewers(reviews: list[dict]) -> str:
    names = []
    seen = set()
    for r in reviews:
        login = r.get("user", {}).get("login", "")
        if login and login not in seen:
            seen.add(login)
            names.append(login)
    return ", ".join(names)


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


# ── Azure DevOps API ───────────────────────────────────────────────────────


def fetch_azure_prs(token: str, org: str, project: str, repo: str, status: str = "active") -> list[dict]:
    b64 = b64encode(f":{token}".encode()).decode()
    headers = {"Authorization": f"Basic {b64}", "Content-Type": "application/json"}
    url = f"https://dev.azure.com/{org}/{project}/_apis/git/repositories/{repo}/pullrequests"
    params = {"searchCriteria.status": status, "$top": 50, "api-version": "7.1-preview.1"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Azure %s/%s: %s", project, repo, e)
        return []
    prs = []
    now = datetime.utcnow()
    for pr in resp.json().get("value", []):
        pr_id = pr["pullRequestId"]
        repo_name = pr.get("repository", {}).get("name", repo)

        # For completed PRs, skip if older than 5 days
        if status == "completed":
            closed_date_str = pr.get("closedDate", "")
            if closed_date_str:
                try:
                    closed_date = datetime.strptime(closed_date_str[:10], "%Y-%m-%d")
                    if (now - closed_date).days > 5:
                        continue
                except ValueError:
                    pass

        reviewers_list = pr.get("reviewers", [])
        review_status = _az_review_status(reviewers_list)
        reviewed_by = ", ".join(
            r.get("displayName", "?") for r in reviewers_list if r.get("displayName")
        )
        has_threads = _az_active_threads(token, org, project, repo_name, pr_id)

        created_str = (pr.get("creationDate") or "")[:10]
        age = 0
        if created_str:
            try:
                age = (now - datetime.strptime(created_str, "%Y-%m-%d")).days
            except ValueError:
                pass

        source_branch = (pr.get("sourceRefName") or "").removeprefix("refs/heads/")
        target_branch = (pr.get("targetRefName") or "").removeprefix("refs/heads/")

        prs.append({
            "source": "Azure DevOps", "repo": f"{project}/{repo_name}",
            "title": pr.get("title", ""),
            "author": pr.get("createdBy", {}).get("displayName", "?"),
            "from_branch": source_branch,
            "into_branch": target_branch,
            "created": created_str,
            "updated": (pr.get("closedDate") or pr.get("creationDate", ""))[:10],
            "age": age,
            "status": review_status,
            "assignee": "",
            "reviewed_by": reviewed_by,
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


# ── Demo ───────────────────────────────────────────────────────────────────


def demo_data() -> list[dict]:
    n = datetime.utcnow()
    d = timedelta
    return [
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "feat: Add new asset upload workflow", "author": "CRI", "from_branch": "feature/asset-upload", "into_branch": "main", "created": (n-d(days=2)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(hours=3)).strftime("%Y-%m-%d"), "age": 2, "assignee": "JHE", "reviewed_by": "PCH, VTO", "url": "https://github.com/myorg/digital-asset/pull/42", "id": "demo:digital-asset:42", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "fix: CORS issue on asset download", "author": "PCH", "from_branch": "fix/cors-download", "into_branch": "main", "created": (n-d(days=5)).strftime("%Y-%m-%d"), "status": "🔧 Changes req.", "updated": (n-d(days=1)).strftime("%Y-%m-%d"), "age": 5, "assignee": "CRI", "reviewed_by": "JHE", "url": "https://github.com/myorg/digital-asset/pull/41", "id": "demo:digital-asset:41", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "refactor: Migrate storage to S3", "author": "JHE", "from_branch": "refactor/s3-migration", "into_branch": "develop", "created": (n-d(days=1)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=6)).strftime("%Y-%m-%d"), "age": 1, "assignee": "", "reviewed_by": "", "url": "https://github.com/myorg/digital-asset/pull/43", "id": "demo:digital-asset:43", "is_draft": True},
        {"source": "Azure DevOps", "repo": "Platform/auth-service", "title": "feat: SSO Azure AD integration", "author": "CRI", "from_branch": "feature/sso-azuread", "into_branch": "main", "created": (n-d(days=3)).strftime("%Y-%m-%d"), "status": "✅⏳ Partial", "updated": (n-d(hours=12)).strftime("%Y-%m-%d"), "age": 3, "assignee": "SPL", "reviewed_by": "PCH, GPI", "url": "https://dev.azure.com/myorg/Platform/_git/auth-service/pullrequest/201", "id": "demo:auth-service:201", "is_draft": False},
        {"source": "Azure DevOps", "repo": "Platform/auth-service", "title": "fix: Token refresh race condition", "author": "PCH", "from_branch": "fix/token-refresh", "into_branch": "main", "created": (n-d(days=7)).strftime("%Y-%m-%d"), "status": "👀 In review", "updated": (n-d(days=2)).strftime("%Y-%m-%d"), "age": 7, "assignee": "VTO", "reviewed_by": "CRI, SRI", "url": "https://dev.azure.com/myorg/Platform/_git/auth-service/pullrequest/200", "id": "demo:auth-service:200", "is_draft": False},
        {"source": "Azure DevOps", "repo": "Platform/notification-svc", "title": "feat: Slack webhook notifications", "author": "JHE", "from_branch": "feature/slack-webhooks", "into_branch": "develop", "created": (n-d(days=1)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=2)).strftime("%Y-%m-%d"), "age": 1, "assignee": "", "reviewed_by": "", "url": "https://dev.azure.com/myorg/Platform/_git/notification-svc/pullrequest/55", "id": "demo:notification-svc:55", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/data-pipeline", "title": "feat: Add dbt models for analytics", "author": "VTO", "from_branch": "feature/dbt-analytics", "into_branch": "main", "created": (n-d(days=4)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(hours=8)).strftime("%Y-%m-%d"), "age": 4, "assignee": "SRI", "reviewed_by": "CRI, JHE", "url": "https://github.com/myorg/data-pipeline/pull/78", "id": "demo:data-pipeline:78", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/data-pipeline", "title": "fix: Handle null values in ETL", "author": "SRI", "from_branch": "fix/null-etl", "into_branch": "main", "created": (n-d(hours=8)).strftime("%Y-%m-%d"), "status": "⏳ Pending", "updated": (n-d(hours=1)).strftime("%Y-%m-%d"), "age": 0, "assignee": "", "reviewed_by": "", "url": "https://github.com/myorg/data-pipeline/pull/79", "id": "demo:data-pipeline:79", "is_draft": False},
    ]


def demo_closed_data() -> list[dict]:
    n = datetime.utcnow()
    d = timedelta
    return [
        {"source": "GitHub", "repo": "myorg/digital-asset", "title": "chore: Update dependencies", "author": "VTO", "from_branch": "chore/deps-update", "into_branch": "main", "created": (n-d(days=6)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(days=1)).strftime("%Y-%m-%d"), "age": 6, "assignee": "", "reviewed_by": "CRI", "url": "https://github.com/myorg/digital-asset/pull/40", "id": "demo:digital-asset:40", "is_draft": False},
        {"source": "Azure DevOps", "repo": "Platform/auth-service", "title": "fix: Password reset email", "author": "GPI", "from_branch": "fix/pwd-reset", "into_branch": "main", "created": (n-d(days=8)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(days=2)).strftime("%Y-%m-%d"), "age": 8, "assignee": "CRI", "reviewed_by": "PCH, JHE", "url": "https://dev.azure.com/myorg/Platform/_git/auth-service/pullrequest/199", "id": "demo:auth-service:199", "is_draft": False},
        {"source": "GitHub", "repo": "myorg/data-pipeline", "title": "docs: Update pipeline README", "author": "SRI", "from_branch": "docs/readme", "into_branch": "main", "created": (n-d(days=5)).strftime("%Y-%m-%d"), "status": "✅ Approved", "updated": (n-d(days=3)).strftime("%Y-%m-%d"), "age": 5, "assignee": "", "reviewed_by": "VTO", "url": "https://github.com/myorg/data-pipeline/pull/77", "id": "demo:data-pipeline:77", "is_draft": False},
    ]


# ── Helpers ────────────────────────────────────────────────────────────────


def _clean(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    return str(val).strip()


def build_repo_df(repo_prs: list[dict], marks: dict, comments: dict) -> pd.DataFrame:
    rows = []
    for pr in sorted(repo_prs, key=lambda p: p["created"], reverse=True):
        draft = " [DRAFT]" if pr["is_draft"] else ""
        row = {
            "Author": pr["author"],
            "From": pr.get("from_branch", ""),
            "Into": pr.get("into_branch", ""),
            "Title": pr["title"] + draft,
            "Created": pr["created"][5:],
            "Modified": pr["updated"][5:],
            "Age": pr.get("age", 0),
            "Status": pr["status"],
            "Assignee": pr.get("assignee", ""),
            "Reviewed by": pr.get("reviewed_by", ""),
            "SPL": marks.get(f"{pr['id']}:SPL", "—"),
            "Note": comments.get(f"cmt:{pr['id']}", ""),
            "Link": pr["url"],
            "_id": pr["id"],
        }
        rows.append(row)
    return pd.DataFrame(rows)


def extract_and_save(edited_df: pd.DataFrame, marks: dict, comments: dict) -> None:
    for _, row in edited_df.iterrows():
        pr_id = row["_id"]
        val = _clean(row.get("SPL", ""))
        marks[f"{pr_id}:SPL"] = val if val else "—"
        comments[f"cmt:{pr_id}"] = _clean(row.get("Note", ""))

    st.session_state["_marks"] = marks
    st.session_state["_comments"] = comments
    save_store(marks, comments)


# ── CSS ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .block-container { padding-top: 0.8rem; padding-bottom: 1rem; }
    section[data-testid="stSidebar"] { display: none !important; }
    button[data-testid="stSidebarCollapsedControl"] { display: none !important; }
    /* Highlight SPL column header */
    [data-testid="stDataEditor"] th:has(> div[title="SPL"]),
    [data-testid="stDataEditor"] [role="columnheader"][title="SPL"] {
        background-color: #7c3aed !important;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    # ── Header ──────────────────────────────────────────────────────────
    logo_col, title_col = st.columns([0.4, 5])
    logo_col.image("logo.svg", width=50)
    title_col.markdown("## PR Dashboard")
    left, right = st.columns([5, 1])
    left.caption("Centralized Pull Request tracking · GitHub & Azure DevOps")
    refresh = right.button("🔄 Refresh", use_container_width=True)

    # ── Source of truth: session_state > file ────────────────────────────
    if "_marks" not in st.session_state or "_comments" not in st.session_state:
        store = load_store()
        st.session_state["_marks"] = store.get("marks", {})
        st.session_state["_comments"] = store.get("comments", {})

    marks: dict = st.session_state["_marks"]
    comments: dict = st.session_state["_comments"]

    # ── Load project mapping ────────────────────────────────────────────
    projects = load_projects()

    # ── Fetch PR data ───────────────────────────────────────────────────
    if "prs" not in st.session_state:
        st.session_state.prs = []
    if "closed_prs" not in st.session_state:
        st.session_state.closed_prs = []

    use_demo = not HAS_TOKENS

    if use_demo:
        if not st.session_state.prs:
            st.session_state.prs = demo_data()
            st.session_state.closed_prs = demo_closed_data()
    else:
        first_load = not st.session_state.prs
        if refresh or first_load:
            all_prs = []
            all_closed = []
            if GH_TOKEN:
                for repo in GH_REPOS:
                    with st.spinner(f"GitHub: {repo}..."):
                        all_prs.extend(fetch_github_prs(GH_TOKEN, repo, state="open"))
                        all_closed.extend(fetch_github_prs(GH_TOKEN, repo, state="closed"))
            if AZ_TOKEN and AZ_ORG:
                for proj_repo in AZ_PROJECTS:
                    parts = proj_repo.split("/")
                    if len(parts) == 2:
                        with st.spinner(f"Azure: {proj_repo}..."):
                            all_prs.extend(fetch_azure_prs(AZ_TOKEN, AZ_ORG, parts[0], parts[1], status="active"))
                            all_closed.extend(fetch_azure_prs(AZ_TOKEN, AZ_ORG, parts[0], parts[1], status="completed"))
            # Deduplicate by PR id (in case a repo appears multiple times in config)
            seen = set()
            st.session_state.prs = [p for p in all_prs if p["id"] not in seen and not seen.add(p["id"])]
            seen_closed = set()
            st.session_state.closed_prs = [p for p in all_closed if p["id"] not in seen_closed and not seen_closed.add(p["id"])]

    prs = st.session_state.prs
    closed_prs = st.session_state.closed_prs

    if not prs and not closed_prs:
        st.info("No PRs found. Check your .env config and click 🔄 Refresh.")
        return

    # ── Filters ─────────────────────────────────────────────────────────
    # Build reverse map: repo -> project
    repo_to_project = {}
    if projects:
        for proj_name, repo_list in projects.items():
            for r in repo_list:
                repo_to_project[r] = proj_name

    all_into_branches = sorted(set(
        pr.get("into_branch", "") for pr in prs + closed_prs if pr.get("into_branch")
    ))

    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        project_options = ["All"] + sorted(projects.keys()) if projects else ["All"]
        selected_project = st.selectbox("Filter by Project", project_options, index=0)
    with filter_col2:
        branch_options = ["All"] + all_into_branches
        selected_branch = st.selectbox("Filter by Target branch", branch_options, index=0)

    def _filter_prs(pr_list: list[dict]) -> list[dict]:
        filtered = pr_list
        if selected_project != "All" and projects:
            allowed_repos = set(projects.get(selected_project, []))
            filtered = [pr for pr in filtered if pr["repo"] in allowed_repos]
        if selected_branch != "All":
            filtered = [pr for pr in filtered if pr.get("into_branch") == selected_branch]
        return filtered

    prs = _filter_prs(prs)
    closed_prs = _filter_prs(closed_prs)

    # ── Group by repo ──────────────────────────────────────────────────
    repos: dict[str, list[dict]] = {}
    for pr in prs:
        repos.setdefault(pr["repo"], []).append(pr)

    closed_repos: dict[str, list[dict]] = {}
    for pr in closed_prs:
        closed_repos.setdefault(pr["repo"], []).append(pr)

    # ── Column config ──────────────────────────────────────────────────
    col_config = {
        "Author": st.column_config.TextColumn("Author", disabled=True),
        "From": st.column_config.TextColumn("From", disabled=True),
        "Into": st.column_config.TextColumn("Into", disabled=True),
        "Title": st.column_config.TextColumn("Title", disabled=True),
        "Created": st.column_config.TextColumn("Created", disabled=True),
        "Modified": st.column_config.TextColumn("Modified", disabled=True),
        "Age": st.column_config.NumberColumn("Age", disabled=True),
        "Status": st.column_config.TextColumn("Status", disabled=True),
        "Assignee": st.column_config.TextColumn("Assignee", disabled=True),
        "Reviewed by": st.column_config.TextColumn("Reviewed by", disabled=True),
        "SPL": st.column_config.SelectboxColumn(
            "SPL", options=EMOJI_OPTIONS, default="—",
        ),
        "Note": st.column_config.TextColumn("Note 📝"),
        "Link": st.column_config.LinkColumn("Link", display_text="↗"),
        "_id": None,
    }

    display_cols = [
        "Author", "From", "Into", "Title", "Created", "Modified",
        "Age", "Status", "Assignee", "Reviewed by", "SPL", "Note", "Link",
    ]

    # ── Display per project > repo ────────────────────────────────────
    all_repo_names = sorted(set(list(repos.keys()) + list(closed_repos.keys())))

    def _render_repo_tables(repo_name: str) -> None:
        """Render open + closed tables for a single repo (no wrapping expander)."""
        repo_prs = repos.get(repo_name, [])
        closed_repo_prs = closed_repos.get(repo_name, [])
        source = (repo_prs or closed_repo_prs or [{}])[0].get("source", "GitHub")
        icon = "🐙" if source == "GitHub" else "🔷"

        if repo_prs:
            count = len(repo_prs)
            st.markdown(f"#### {icon} {repo_name} · {count} PR{'s' if count > 1 else ''}")
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
            extract_and_save(edited, marks, comments)

        if closed_repo_prs:
            count_closed = len(closed_repo_prs)
            with st.expander(f"📦 {repo_name} · {count_closed} Closed (last 5 days)", expanded=False):
                df_closed = build_repo_df(closed_repo_prs, marks, comments)
                edited_closed = st.data_editor(
                    df_closed,
                    column_config=col_config,
                    column_order=display_cols,
                    hide_index=True,
                    use_container_width=True,
                    key=f"ed_closed_{repo_name}",
                    num_rows="fixed",
                )
                extract_and_save(edited_closed, marks, comments)

    if projects:
        # Group repos by project — project expander > repo tables inside
        rendered_repos = set()
        for proj_name in sorted(projects.keys()):
            proj_repos = projects[proj_name]
            proj_repo_names = [r for r in proj_repos if r in repos or r in closed_repos]
            if not proj_repo_names:
                continue
            total_open = sum(len(repos.get(r, [])) for r in proj_repo_names)
            total_closed_proj = sum(len(closed_repos.get(r, [])) for r in proj_repo_names)
            label = f"📁 **{proj_name}** · {total_open} open"
            if total_closed_proj:
                label += f" · {total_closed_proj} closed"
            with st.expander(label, expanded=True):
                for repo_name in sorted(proj_repo_names):
                    _render_repo_tables(repo_name)
                    rendered_repos.add(repo_name)

        # Repos not mapped to any project
        unmapped = [r for r in all_repo_names if r not in rendered_repos]
        if unmapped:
            total_open_other = sum(len(repos.get(r, [])) for r in unmapped)
            with st.expander(f"📁 **Other** · {total_open_other} open", expanded=True):
                for repo_name in unmapped:
                    _render_repo_tables(repo_name)
    else:
        # No projects.json — flat list
        for repo_name in all_repo_names:
            _render_repo_tables(repo_name)

    # ── Footer ─────────────────────────────────────────────────────────
    total_open = len(st.session_state.prs)
    total_closed = len(st.session_state.closed_prs)
    mode = "🎭 Demo" if use_demo else "🔴 Live"
    st.caption(f"{total_open} open · {total_closed} closed · {len(all_repo_names)} repos · {mode}")


if __name__ == "__main__":
    main()
