"""Decentralized Runtime Integrity Engine: monitoring agent + live dashboard.

Flow:
  1. User uploads file(s) via the dashboard into target_system/.
  2. The SHA-256 hash of the ENTIRE target_system/ directory is anchored on-chain.
  3. The live monitoring dashboard watches the whole directory every second.
  4. If ANY file inside target_system/ is added, modified, or deleted, the change
     is detected immediately and a tamper alert is written on-chain.

States:
  VERIFIED               live directory hash == on-chain hash
  AUTHORIZED UPDATE      developer approved on-chain, agent anchored new hash
  UNAUTHORIZED MOD       hashes differ, no approval -> tamper alert written on-chain

Run:  streamlit run app.py
"""
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

import streamlit as st

import styles
import target
from hash_utils import sha256_directory, directory_manifest, diff_manifests
from ledger import DEPLOYMENT_FILE, ChainOffline, Ledger, NotDeployed

POLL_SECONDS = 1.0        # how often the agent re-checks the directory
BLUE_HOLD_SECONDS = 6     # keep the "Authorized update" banner visible after an anchor
MISSING = "dir-missing"   # pseudo-hash when the target directory is gone

DEVELOPER_PASSWORD = os.environ.get("DEVELOPER_PASSWORD", "Axion")

ROOT = Path(__file__).parent
TARGET_DIR = ROOT / "target_system"
STATE_DIR = ROOT / ".state"
MANIFEST_PATH = STATE_DIR / "trusted_manifest.json"

st.set_page_config(page_title="Runtime Integrity Engine", page_icon="🛡️",
                   layout="wide", initial_sidebar_state="auto")
st.markdown(styles.CSS, unsafe_allow_html=True)


# ------------------------------------------------------------------ shared resources
@st.cache_resource
def engine_lock():
    return threading.Lock()


@st.cache_resource
def shared_state():
    """Cross-rerun mutable state shared by all sessions.

    Keys:
      last_update_diff  – file changes captured at the moment of an authorized anchor
      last_reported_hash – the most recent live hash for which a TamperDetected event
                           was emitted.  Reset to "" whenever the live hash returns to
                           the trusted hash, so a re-occurrence IS re-reported.
    """
    return {
        "last_update_diff": [],
        "last_reported_hash": "",
    }


@st.cache_resource(show_spinner=False)
def _cached_ledger(deployment_stamp):
    return Ledger()


def get_ledger():
    """Return (ledger, None) or (None, "offline" | "undeployed")."""
    stamp = DEPLOYMENT_FILE.stat().st_mtime if DEPLOYMENT_FILE.exists() else 0
    try:
        return _cached_ledger(stamp), None
    except ChainOffline:
        return None, "offline"
    except NotDeployed:
        return None, "undeployed"
    except Exception:
        return None, "offline"


def live_hash():
    """Hash the entire target_system/ directory."""
    try:
        h = sha256_directory(TARGET_DIR)
        return h if h else MISSING
    except OSError:
        return MISSING


def local_time(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%H:%M:%S")


def short(h):
    return h[:8] if h else "—"


# --------------------------------------------------------- snapshot / manifest helpers
def save_manifest():
    """Save the current directory manifest as the trusted reference."""
    STATE_DIR.mkdir(exist_ok=True)
    manifest = directory_manifest(TARGET_DIR)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))


def load_manifest():
    """Load the last trusted manifest."""
    try:
        return json.loads(MANIFEST_PATH.read_text())
    except (OSError, ValueError):
        return {}


def diff_vs_trusted():
    """Return list of (change_type, filepath) between trusted manifest and live directory."""
    old = load_manifest()
    try:
        new = directory_manifest(TARGET_DIR)
    except OSError:
        return [("deleted", f) for f in old]
    return diff_manifests(old, new)


# ----------------------------------------------------------------------- the engine
@dataclass
class Status:
    state: str                  # "verified" | "authorized" | "tamper"
    live: str
    trusted: str
    trail: list
    window_open: bool = False   # approval granted but file not changed yet
    event: object = None        # the trail entry that explains the state
    changes: list = field(default_factory=list)


def run_agent(ledger):
    """One monitoring cycle: hash, compare, act on-chain, classify."""
    ss = shared_state()

    with engine_lock():
        lh = live_hash()
        trusted = ledger.trusted_hash()

        if lh == trusted:
            # System is clean — reset the dedup tracker so future changes are reportable.
            ss["last_reported_hash"] = ""
        elif lh != trusted:
            if ledger.update_authorized():
                # 🔵 Approved change: anchor the new fingerprint, consuming the approval.
                ss["last_update_diff"] = diff_vs_trusted()
                ledger.update_hash(lh)
                save_manifest()
                trusted = lh
                ss["last_reported_hash"] = ""
            else:
                # 🔴 Unapproved change: write the alert on-chain once per distinct bad hash.
                # Only suppress if we already reported THIS EXACT hash in this tampered session.
                if ss["last_reported_hash"] != lh:
                    ledger.report_tamper(lh)
                    ss["last_reported_hash"] = lh

        trail = ledger.audit_trail()

    if lh != trusted:
        alert = next((e for e in reversed(trail) if e.kind == "tamper" and e.hash == lh), None)
        return Status("tamper", lh, trusted, trail, event=alert, changes=diff_vs_trusted())

    last_anchor = next((e for e in reversed(trail) if e.kind == "anchored"), None)
    if last_anchor and last_anchor.by == ledger.agent and time.time() - last_anchor.timestamp < BLUE_HOLD_SECONDS:
        return Status("authorized", lh, trusted, trail, event=last_anchor,
                      changes=ss["last_update_diff"])
    return Status("verified", lh, trusted, trail, window_open=ledger.update_authorized())


# --------------------------------------------------------------------- HTML pieces
def html_header(online):
    live_el = (f'<div class="live"><span class="dot"></span>Live <span class="mono">{local_time()}</span></div>'
            if online else '<div class="live off"><span class="dot"></span>Offline</div>')
    return ('<div class="topbar"><div><div class="title">Runtime integrity engine</div>'
            '<div class="subtitle">SHA-256 verification anchored on a blockchain</div></div>'
            f'{live_el}</div>')


def html_file_changes(changes, limit=6):
    """Render file-level changes (added/modified/deleted) instead of JSON key diffs."""
    if not changes:
        return ""
    type_icons = {"modified": "✏️", "added": "➕", "deleted": "🗑️"}
    type_cls = {"modified": "fc-modified", "added": "fc-added", "deleted": "fc-deleted"}
    rows = "".join(
        f'<div class="file-change {type_cls.get(ctype, "")}">'
        f'<span class="fc-icon">{type_icons.get(ctype, "•")}</span>'
        f'<span class="fc-type">{ctype.upper()}</span>'
        f'<span class="fc-path">{escape(fpath)}</span></div>'
        for ctype, fpath in changes[:limit])
    extra = f'<div class="fc-more">+ {len(changes) - limit} more…</div>' if len(changes) > limit else ""
    return f'<div class="file-changes">{rows}{extra}</div>'


def html_status(s):
    if s.state == "verified":
        meta = f"Last check {local_time()} Local"
        if s.window_open:
            meta += " · update window open, waiting for the release"
        return _banner("s-verified", styles.ICON_VERIFIED, "Verified",
                       "All files in the monitored directory match the on-chain cryptographic reference.", meta)

    if s.state == "authorized":
        e = s.event
        return _banner("s-authorized", styles.ICON_AUTHORIZED, "Authorized update",
                       "Developer approval found on-chain. New fingerprint anchored; approval consumed.",
                       f"Anchored in block {e.block} at {local_time(e.timestamp)} Local by Agent-01",
                       html_file_changes(s.changes))

    # tamper state
    e = s.event
    meta = (f"Tamper alert written on-chain in block {e.block} at {local_time(e.timestamp)} Local"
            if e else "Writing tamper alert on-chain…")
    n_files = len(s.changes)
    msg = (f"{n_files} file{'s' if n_files != 1 else ''} changed in target_system/ without authorization."
           if n_files > 0 else
           "target_system/ no longer matches the on-chain reference, and no update was authorized.")
    return _banner("s-tamper", styles.ICON_TAMPER, "Unauthorized modification", msg, meta,
                   html_file_changes(s.changes))


def html_problem(problem):
    if problem == "undeployed":
        title, msg = "Contract not deployed", "The chain is up, but IntegrityLedger isn't deployed on it."
        cmd = ".venv/bin/python deploy.py"
    else:
        title, msg = "Blockchain offline", "Can't reach the local node at http://127.0.0.1:8545."
        cmd = "npx hardhat node<br>.venv/bin/python deploy.py"
    return _banner("s-offline", styles.ICON_OFFLINE, title, msg, "Retrying every second",
                   f'<div class="cmd">{cmd}</div>')


def _banner(cls, icon, title, msg, meta, extra=""):
    return (f'<div class="status {cls}">{icon}<div style="min-width:0">'
            f'<div class="status-title">{title}</div><div class="status-msg">{msg}</div>'
            f'<div class="status-meta">{meta}</div>{extra}</div></div>')


def _grouped(h, cls=""):
    if len(h) != 64:
        return f'<span class="{cls}">{escape(h)}</span>'
    return " ".join(f'<span class="{cls}">{h[i:i + 8]}</span>' for i in range(0, 64, 8))


def html_hashes(s):
    bad = "bad" if s.state == "tamper" else ""
    path = "./target_system/"
    return (f'<div class="card"><div class="card-head"><span>Directory hash comparison</span>'
            f'<span class="mono">{path}</span></div>'
            f'<div class="hash-row"><div class="hash-label">Live directory {short(s.live)}...</div>'
            f'<div class="hash">{_grouped(s.live, bad)}</div></div>'
            f'<div class="hash-row"><div class="hash-label">On-chain trusted {short(s.trusted)}...</div>'
            f'<div class="hash">{_grouped(s.trusted)}</div></div></div>')


def html_file_list():
    """Show the current contents of the monitored directory."""
    if not TARGET_DIR.is_dir():
        return ""
    try:
        files = sorted(f for f in TARGET_DIR.rglob("*") if f.is_file())
    except OSError:
        return ""
    if not files:
        return ""
    rows = "".join(
        f'<div class="fl-row"><span class="fl-name">{escape(f.relative_to(TARGET_DIR).as_posix())}</span>'
        f'<span class="fl-size">{f.stat().st_size:,} bytes</span></div>'
        for f in files[:20])
    count = len(files)
    extra = f'<div class="fl-more">+ {count - 20} more…</div>' if count > 20 else ""
    return (f'<div class="card"><div class="card-head"><span>📂 Monitored directory</span>'
            f'<span class="mono">{count} file{"s" if count != 1 else ""}</span></div>'
            f'<div class="fl-hint">Edit, add, or delete any file below to see tampering detected in real-time.</div>'
            f'{rows}{extra}</div>')


_KIND = {"anchored": ("Anchored", "st-anchored"),
         "authorized": ("Approved", "st-authorized"),
         "tamper": ("Tamper detected", "st-tamper")}


def html_trail(s, ledger, max_blocks=4, max_rows=8):
    per_block = {ledger.deploy_block: ("Deploy", "")}
    for e in s.trail:
        label = {"anchored": short(e.hash), "authorized": "Approval", "tamper": "⚠ " + short(e.hash)}[e.kind]
        per_block[e.block] = (label, e.kind)
    recent = sorted(per_block.items())[-max_blocks:]
    cards = f'<div class="chain-link">{styles.ICON_LINK}</div>'.join(
        f'<div class="blk {kind}"><div class="blk-n">Block {n}</div><div class="blk-v">{label}</div></div>'
        for n, (label, kind) in recent)

    rows = "".join(
        f'<tr><td class="mono">{local_time(e.timestamp)}</td>'
        f'<td class="mono">{short(e.hash) if e.hash else "—"}</td>'
        f'<td class="{_KIND[e.kind][1]}">{_KIND[e.kind][0]}</td>'
        f'<td>{ledger.label(e.by)}</td></tr>'
        for e in list(reversed(s.trail))[:max_rows])

    addr = f"{ledger.address[:10]}...{ledger.address[-4:]}"
    return (f'<div class="card"><div class="card-head"><span>Blockchain audit trail</span>'
            f'<span class="mono">{addr}</span></div><div class="blocks">{cards}</div>'
            '<div class="table-wrap"><table class="trail"><thead><tr><th>Time (Local)</th><th>Hash</th>'
            f'<th>Status</th><th>By</th></tr></thead><tbody>{rows}</tbody></table></div></div>')


# ------------------------------------------------------------------ upload page
def show_upload_page():
    """The initial upload screen — user uploads files into target_system/ before monitoring."""
    st.html('''
    <div class="upload-page">
        <div class="upload-hero">
            <div class="upload-icon">🛡️</div>
            <div class="upload-title">Runtime Integrity Engine</div>
            <div class="upload-subtitle">Upload files to the monitored directory and anchor the folder hash on-chain</div>
        </div>
    </div>
    ''')

    # Show existing files in target_system/
    existing = sorted(f for f in TARGET_DIR.rglob("*") if f.is_file()) if TARGET_DIR.is_dir() else []
    if existing:
        st.html(f'''
        <div class="upload-existing">
            <div class="upload-preview-title">Files already in target_system/ ({len(existing)})</div>
        </div>
        ''')
        for f in existing[:15]:
            rel = f.relative_to(TARGET_DIR).as_posix()
            size = f.stat().st_size
            st.html(f'<div class="upload-detail"><span class="mono">{escape(rel)}</span> — <span class="upload-label">{size:,} bytes</span></div>')

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        uploaded_files = st.file_uploader(
            "Add files to target_system/ (optional)",
            type=None,
            key="file_upload",
            accept_multiple_files=True,
            help="Upload additional files to protect. They'll be saved into target_system/. You can also skip this and anchor the existing files."
        )

        if uploaded_files:
            st.html(f'<div class="upload-preview"><div class="upload-preview-title">{len(uploaded_files)} file(s) ready to add</div></div>')
            for uf in uploaded_files:
                file_bytes = uf.read()
                uf.seek(0)  # reset for later use
                st.html(f'<div class="upload-detail"><span class="mono">{escape(uf.name)}</span> — <span class="upload-label">{len(file_bytes):,} bytes</span></div>')

        st.html('<div style="height: 0.5rem"></div>')

        if st.button("🔗 Anchor directory on blockchain", key="btn_anchor", use_container_width=True, type="primary"):
            _anchor_directory(uploaded_files or [])


def _anchor_directory(uploaded_files):
    """Save any new uploads to target_system/, then anchor the whole directory hash on-chain."""
    ledger, problem = get_ledger()
    if ledger is None:
        st.error(f"Cannot anchor: blockchain is {'offline' if problem == 'offline' else 'not deployed'}.")
        return

    # Save uploaded files to target_system/
    TARGET_DIR.mkdir(exist_ok=True)
    for uf in uploaded_files:
        dest = TARGET_DIR / uf.name
        dest.write_bytes(uf.read())

    # Compute directory hash
    dir_hash = sha256_directory(TARGET_DIR)
    if not dir_hash:
        st.error("target_system/ is empty. Upload at least one file.")
        return

    # Anchor on-chain
    try:
        ledger.authorize_update()
        ledger.update_hash(dir_hash)
    except Exception as e:
        st.error(f"On-chain anchoring failed: {e}")
        return

    # Save the trusted manifest
    save_manifest()

    # Reset the dedup tracker so the fresh session starts clean
    shared_state()["last_reported_hash"] = ""

    # Mark as anchored
    st.session_state["dir_anchored"] = True
    st.toast("✅ Directory anchored on-chain!", icon="🔗")
    st.rerun()


# ------------------------------------------------------------------ developer auth UI
def show_developer_auth(status, ledger):
    """Show the developer authorization panel when tampering is detected."""
    st.html('''
    <div class="auth-panel">
        <div class="auth-header">
            <div class="auth-icon">🔐</div>
            <div class="auth-title">Developer Verification Required</div>
            <div class="auth-desc">This modification has not been authorized. Enter developer credentials to approve the current change and anchor the new fingerprint on-chain.</div>
        </div>
    </div>
    ''')

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        password = st.text_input(
            "Developer ID / Password",
            type="password",
            key="dev_password",
            placeholder="Enter developer credential",
        )

        if st.button("🔓 Authorize This Change", key="btn_authorize", use_container_width=True, type="primary"):
            if not password:
                st.session_state["auth_msg"] = ("error", "No credential entered. The modification remains unauthorized.")
            elif password != DEVELOPER_PASSWORD:
                st.session_state["auth_msg"] = ("error", "Developer authentication failed. The modification remains unauthorized.")
            else:
                # Capture the current live hash at the moment of approval
                current_live = live_hash()
                if current_live == MISSING:
                    st.session_state["auth_msg"] = ("error", "Cannot authorize: target directory is missing.")
                elif current_live == status.trusted:
                    st.session_state["auth_msg"] = ("info", "Files have already been restored to the trusted state. No authorization needed.")
                elif current_live != status.live:
                    # Race condition: files changed between detection and approval
                    st.session_state["auth_msg"] = ("error", "The files changed again since the tamper was detected. Please review the new state and re-authorize.")
                else:
                    try:
                        # Capture file changes BEFORE authorization so we can display them in BLUE state
                        shared_state()["last_update_diff"] = diff_vs_trusted()

                        # Developer authorization on-chain (two-key model)
                        ledger.authorize_update()
                        # Agent anchors the new hash (consumes the approval)
                        ledger.update_hash(current_live)
                        # Save the new trusted manifest
                        save_manifest()
                        # Reset dedup tracker
                        shared_state()["last_reported_hash"] = ""

                        st.session_state["auth_msg"] = ("success", "Developer authorization verified. New fingerprint anchored on-chain by Agent-01.")
                        # Clear the password field
                        if "dev_password" in st.session_state:
                            del st.session_state["dev_password"]
                        st.rerun()
                    except Exception as e:
                        st.session_state["auth_msg"] = ("error", f"On-chain authorization failed: {e}")

        # Show any auth feedback message
        msg_info = st.session_state.get("auth_msg")
        if msg_info:
            msg_type, msg_text = msg_info
            if msg_type == "error":
                st.html(f'''
                <div class="auth-feedback auth-error">
                    <span class="auth-fb-icon">✗</span>
                    <span>{escape(msg_text)}</span>
                </div>
                ''')
            elif msg_type == "success":
                st.html(f'''
                <div class="auth-feedback auth-success">
                    <span class="auth-fb-icon">✓</span>
                    <span>{escape(msg_text)}</span>
                </div>
                ''')
            elif msg_type == "info":
                st.html(f'''
                <div class="auth-feedback auth-info">
                    <span class="auth-fb-icon">ℹ</span>
                    <span>{escape(msg_text)}</span>
                </div>
                ''')


# ------------------------------------------------------------------ sidebar: demo panel
ledger, problem = get_ledger()
dir_is_anchored = st.session_state.get("dir_anchored", False)

if dir_is_anchored:
    with st.sidebar:
        st.html('<div class="side-title">Demo panel</div><div class="side-sub">Monitoring: target_system/</div>')

        upload_new = st.button("Re-anchor directory", icon=":material/upload_file:", key="btn_upload_new", width="stretch")

    if upload_new:
        st.session_state["dir_anchored"] = False
        # Clear any auth messages
        if "auth_msg" in st.session_state:
            del st.session_state["auth_msg"]
        st.rerun()

    with st.sidebar:
        st.divider()
        chain = f"Hardhat, chain {ledger.chain_id}" if ledger else "Hardhat, offline"
        file_count = len(list(TARGET_DIR.rglob("*"))) if TARGET_DIR.is_dir() else 0
        st.html(f'<div class="side-info">Agent-01 (Production)<br>target_system/ ({file_count} files)<br>{chain}</div>')


# ------------------------------------------------------------- main: page routing
if not dir_is_anchored:
    show_upload_page()
else:
    @st.fragment(run_every=POLL_SECONDS)
    def dashboard():
        ledger, problem = get_ledger()
        status = None
        if ledger:
            try:
                status = run_agent(ledger)
            except Exception:
                _cached_ledger.clear()
                problem = "offline"

        st.html(html_header(online=status is not None))
        if status is None:
            st.html(html_problem(problem))
            return
        st.html(html_status(status))

        # Show developer authorization panel when tampering is detected
        if status.state == "tamper":
            show_developer_auth(status, ledger)
            # Clear any stale success messages when in tamper state
            if st.session_state.get("auth_msg", (None,))[0] == "success":
                del st.session_state["auth_msg"]
        else:
            # Clear auth messages when not in tamper state
            if "auth_msg" in st.session_state:
                del st.session_state["auth_msg"]

        file_list = html_file_list()
        if file_list:
            st.html(file_list)
        st.html(html_hashes(status))
        st.html(html_trail(status, ledger))

    dashboard()
