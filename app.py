import streamlit as st
import json
import os
import time
# import base64  # Google authentication is disabled.
import hashlib
from uuid import uuid4
import streamlit.components.v1 as components
from audio_generation import SCRIPT_VERSION, GenerationError, generate_script, render_tracks, save_session
from audio_player import player_html
from duration_picker import minute_wheel
import measurement_ui
from pathlib import Path
from html import escape
from dotenv import load_dotenv
# Google authentication is intentionally disabled; retain for later use.
# try:
#     import extra_streamlit_components as stx
#     from streamlit_oauth import OAuth2Component
# except ImportError:
#     stx = None
#     OAuth2Component = None

st.set_page_config(page_title="Flowy UI App", layout="wide")

# ==========================================
# 0. CONFIGURATION & DATABASE
# ==========================================

HERE = Path(__file__).resolve().parent
from deployment_config import load_database_secrets
from streamlit.errors import StreamlitSecretNotFoundError
try:
    load_database_secrets(st.secrets)
except StreamlitSecretNotFoundError:
    pass  # Local development can use environment variables or .env files.
load_dotenv(HERE / ".env", override=False)
load_dotenv(HERE / ".env.local", override=False)
from account_database import AccountDatabase
import chronos_store as cloud_store
from cloud_storage_ui import show_audio_storage
# CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
# CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
# TOKEN_URL = "https://oauth2.googleapis.com/token"
# REVOKE_TOKEN_URL = "https://oauth2.googleapis.com/revoke"

def account_database():
    if "account_database" not in st.session_state:
        st.session_state.account_database = AccountDatabase(HERE)
    return st.session_state.account_database


def load_db():
    return account_database().current()


def save_db(db):
    account_database().save_current(db)


def apply_account(profile):
    st.session_state.update(
        user_id=profile["user_id"], user_email=profile["identity"],
        measurement_identity=profile["identity"], username=profile["name"],
        signed_up=True, guest=False, answers=profile["answers"],
        questionnaire_done=profile["questionnaire_done"], current_q=0,
        session_q=0, session_answers={})
    if profile.get("narrator") in VOICE_ENV_KEYS:
        st.session_state.selected_narrator = profile["narrator"]
    cm.set("auth_user", profile["identity"])


# Cookie manager without the cache warning
class SessionCookies:
    def get(self, cookie):
        return None
    def set(self, *args):
        pass
    def delete(self, *args):
        pass

# Authentication uses the verified server-side session. The old username
# cookie was never trusted for login and is unnecessary here.
cm = SessionCookies()

# ==========================================
# 1. DATA STRUCTURES & SESSION STATE
# ==========================================
THEMES = {
    "🌲": "https://codepen.io/nerdmanship/embed/xgmBKg?default-tab=result&theme-id=dark",
    "🌌": "https://codepen.io/a-trost/embed/pXzbbq?default-tab=result&theme-id=dark"
}
THEME_LABELS = {"🌲": "Forest", "🌌": "Roadtrip Sunset"}

def read_questionnaire(filename):
    with (HERE / "data" / filename).open(encoding="utf-8") as file:
        document = json.load(file)
    for question in document["items"]:
        if set(question["options"]) != {"A", "B"}:
            raise ValueError(f"Invalid options for {question['id']}")
    return document

INTRO = read_questionnaire("intro questionnaire.json")
SESSION = read_questionnaire("session questionnaire.json")
QUESTIONS = INTRO["items"]
VOICE_ENV_KEYS = {
    "Male voice 1": "ELEVENLABS_MALE_VOICE_1_ID",
    "Male voice 2": "ELEVENLABS_MALE_VOICE_2_ID",
    "Female voice 1": "ELEVENLABS_FEMALE_VOICE_1_ID",
    "Female voice 2": "ELEVENLABS_FEMALE_VOICE_2_ID",
}

def clear_generated_session():
    measurement_ui.revoke_player()
    for key in ("generated_session", "generated_script", "script_plan_hash", "database_session_id"):
        st.session_state.pop(key, None)


def update_narrator():
    if "audio_plan" in st.session_state:
        plan = dict(st.session_state.audio_plan)
        plan["voice_choice"] = st.session_state.selected_narrator if plan["session_choices"].get("narration_enabled") else None
        st.session_state.audio_plan = plan
    clear_generated_session()


if "bg_theme" not in st.session_state: st.session_state.bg_theme = "🌲"
if "current_q" not in st.session_state: st.session_state.current_q = 0

for key, value in {
    "signed_up": False, "questionnaire_done": False, "answers": [],
    "username": "", "user_email": "", "guest": False,
}.items():
    st.session_state.setdefault(key, value)

if st.session_state.signed_up and not st.session_state.guest and not account_database().user_id:
    measurement_ui.revoke_player()
    st.session_state.signed_up = False
    st.session_state.pop("measurement_identity", None)
    st.session_state.auth_view = "Sign In"

# Always show the welcome choices on a new visit. A saved username cookie
# is not a sign-in and must not silently skip this page.

expected_fields = {q["field"] for q in QUESTIONS}
valid_fields = {a.get("field") for a in st.session_state.answers if isinstance(a, dict)}
if st.session_state.questionnaire_done and not expected_fields.issubset(valid_fields):
    st.session_state.questionnaire_done = False
    st.session_state.answers = []
    st.session_state.current_q = 0

# ==========================================
# 2. GLOBAL STYLING & SIDEBAR
# ==========================================
FLOWY_UI_CSS = """
    <style>
    .flowy-question {
        font-size: 3.5rem !important; font-weight: 800; text-align: center;
        margin-top: 8vh; margin-bottom: 5vh; line-height: 1.3;
        animation: floatUp 0.8s cubic-bezier(0.25, 0.8, 0.25, 1) forwards;
        opacity: 0; text-shadow: 0px 4px 15px rgba(0,0,0,0.5);
    }
    :is(.st-key-answer_a, .st-key-answer_b, .st-key-session_answer_a, .st-key-session_answer_b) button {
        height: 250px; width: 100%; font-size: 3.5rem !important; font-weight: bold;
        border-radius: 30px; background: rgba(255, 255, 255, 0.1); backdrop-filter: blur(10px);
        border: 2px solid rgba(255, 255, 255, 0.2); transition: all 0.3s cubic-bezier(0.25, 0.8, 0.25, 1);
        animation: floatUp 0.8s cubic-bezier(0.25, 0.8, 0.25, 1) forwards;
        opacity: 0; animation-delay: 0.1s;
    }
    :is(.st-key-answer_a, .st-key-answer_b, .st-key-session_answer_a, .st-key-session_answer_b) button:hover {
        transform: translateY(-10px) scale(1.02); background: rgba(255, 255, 255, 0.25);
        border: 2px solid white; box-shadow: 0 20px 40px rgba(0,0,0,0.4);
    }
    :is(.st-key-answer_a, .st-key-answer_b, .st-key-session_answer_a, .st-key-session_answer_b) button {
        padding: 1.5rem 2rem; white-space: normal;
        display: flex; align-items: center; justify-content: center;
    }
    :is(.st-key-answer_a, .st-key-answer_b, .st-key-session_answer_a, .st-key-session_answer_b) button p {
        font-size: clamp(1rem, 2vw, 1.65rem) !important;
        font-weight: bold; line-height: 1.35; text-align: center;
        white-space: normal; overflow-wrap: anywhere; margin: 0;
    }
    @keyframes floatUp { 0% { opacity: 0; transform: translateY(40px); } 100% { opacity: 1; transform: translateY(0); } }
    .progress-text { text-align: center; font-size: 1.2rem; opacity: 0.7; letter-spacing: 2px; }
    </style>
    """

pill_button_css = """
<style>
/* Streamlit's hosting button is unrelated to the profile's saved files. */
.stAppDeployButton, [data-testid="stAppDeployButton"] { display: none !important; }
[data-testid="stSidebar"] div.stButton { width: 100%; min-width:0; }
[data-testid="stSidebar"] div.stButton > button {
    width: 100% !important; border-radius: 13px !important; padding: 8px !important;
    height: 4rem !important; min-height: 4rem !important; border: 1px solid rgba(255, 255, 255, 0.2);
    background-color: rgba(0, 0, 0, 0.5); color: white; transition: all 0.2s;
}
[data-testid="stSidebar"] div.stButton > button:hover {
    border: 1px solid white; background-color: rgba(255, 255, 255, 0.1);
}
[data-testid="stSidebar"] { background-color: rgba(0, 0, 0, 0.6) !important; }

#scene-background {
    position: fixed; inset: 0; overflow: hidden; z-index: -999;
    pointer-events: none; background: #1e1423;
}
#codepen-iframe {
    position: absolute; top: -65px; left: 0; width: 100vw;
    height: calc(100vh + 130px); border: none; pointer-events: none;
}
/* The sunset artwork is inset inside its embed. Crop those margins like
   object-fit: cover, keeping its proportions on wide and tall screens. */
#scene-background.sunset #codepen-iframe {
    top: 50%; left: 50%;
    width: max(100vw, 177.778vh); height: max(100vh, 56.25vw);
    transform: translate(-50%, -50%) scale(1.6);
    transform-origin: center;
}
#scene-background.sunset::after {
    content: ""; position: absolute; inset: 0;
    background: rgba(0, 0, 0, 0.4);
}
[data-testid="stAppViewContainer"], [data-testid="stApp"], [data-testid="stHeader"] {
    background: transparent !important; background-color: transparent !important;
}
h1, h2, h3, p, span, label, div { color: white !important; }
</style>
"""
st.markdown(pill_button_css, unsafe_allow_html=True)

st.markdown("""<style>
[data-testid="stSidebar"] .stButton button {min-height:3.5rem; width:100%;}
[data-testid="stSidebar"] .stButton button p {font-size:1rem !important; line-height:1.3; white-space:normal !important; overflow-wrap:anywhere; text-align:center;}
</style>""", unsafe_allow_html=True)
with st.sidebar:
    st.write("### Themes")
    theme_columns = st.columns(2)
    for theme_column, theme_name in zip(theme_columns, THEMES):
        if theme_column.button(f"{theme_name} {THEME_LABELS[theme_name]}", key=f"theme_{theme_name}", use_container_width=True,
                     type="primary" if st.session_state.bg_theme == theme_name else "secondary"):
            st.session_state.bg_theme = theme_name
            st.rerun()

background_class = "sunset" if st.session_state.bg_theme == "🌌" else "forest"
st.markdown(
    f'<div id="scene-background" class="{background_class}" aria-hidden="true">'
    f'<iframe id="codepen-iframe" title="{THEME_LABELS[st.session_state.bg_theme]} background" '
    f'src="{THEMES[st.session_state.bg_theme]}" tabindex="-1" '
    'sandbox="allow-scripts allow-same-origin"></iframe></div>',
    unsafe_allow_html=True,
)

# ==========================================
# 3. MAIN APP LOGIC
# ==========================================

# --- STEP 1: COMBINED AUTHENTICATION ---
if not st.session_state.get("signed_up", False):

    # Username/password authentication is the default, including existing tabs
    # whose session state still points to the disabled Google view.
    if st.session_state.get("auth_view") not in ("Sign In", "Sign Up"):
        st.session_state.auth_view = "Sign In"

    step1_css = """
    <style>
    [data-testid="stForm"] {
        background-color: rgba(0, 0, 0, 0.6); border-radius: 15px;
        padding: 2rem; border: 1px solid rgba(255,255,255,0.1);
    }
    .nav-col button {
        border: none !important; background: transparent !important;
        text-align: left !important; font-size: 1.1rem !important; border-radius: 8px !important;
    }
    .nav-col button:hover { background: rgba(255,255,255,0.1) !important; }
    </style>
    """
    st.markdown(step1_css, unsafe_allow_html=True)

    st.title("Welcome to Chronos ✨")
    if st.button("Continue as guest"):
        st.session_state.update(signed_up=True, guest=True, username="Guest")
        st.rerun()
    st.write("---")

    menu_col, content_col = st.columns([1, 3])

    with menu_col:
        st.markdown('<div class="nav-col">', unsafe_allow_html=True)
        # Google sign-in is disabled.
        # if st.button("🌐 Google Login", use_container_width=True):
        #     st.session_state.auth_view = "Google Login"
        if st.button("🔑 Sign In", use_container_width=True):
            st.session_state.auth_view = "Sign In"
        if st.button("📝 Sign Up", use_container_width=True):
            st.session_state.auth_view = "Sign Up"
        st.markdown('</div>', unsafe_allow_html=True)

    with content_col:
        # if st.session_state.auth_view == "Google Login":
        #     st.subheader("Google Login")
        #     st.write("Sign in securely with your Google account:")
        #
        #     if not CLIENT_ID or not CLIENT_SECRET or OAuth2Component is None:
        #         pass  # Keep configuration details off the welcome page.
        #     else:
        #         try:
        #             # Because this is now the default view, it will ALWAYS run when Google redirects back!
        #             oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_URL, TOKEN_URL, TOKEN_URL, REVOKE_TOKEN_URL)
        #
        #             result = oauth2.authorize_button(
        #                 name="Sign in with Google",
        #                 icon="https://www.google.com/favicon.ico",
        #                 redirect_uri="http://localhost:8501",
        #                 scope="openid email profile",
        #                 key="google_login",
        #                 use_container_width=True
        #             )
        #
        #             if result and "token" in result:
        #                 st.session_state.token = result.get("token")
        #
        #                 id_token = result["token"]["id_token"]
        #                 payload = id_token.split(".")[1]
        #                 payload += "=" * ((4 - len(payload) % 4) % 4)
        #                 decoded_payload = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        #
        #                 # Verify Google's signature/audience before using this account for private results.
        #                 from google.oauth2 import id_token as google_id_token
        #                 from google.auth.transport.requests import Request as GoogleAuthRequest
        #                 decoded_payload = google_id_token.verify_oauth2_token(id_token, GoogleAuthRequest(), CLIENT_ID)
        #                 if not decoded_payload.get("email_verified"):
        #                     raise ValueError("Google email is not verified.")
        #                 user_email = decoded_payload.get("email")
        #                 user_name = decoded_payload.get("name", "Google User")
        #
        #                 profile = account_database().google(decoded_payload["sub"], user_email, user_name)
        #                 apply_account(profile)
        #
        #                 # Clean up URL parameters after successful login
        #                 if hasattr(st, "query_params"):
        #                     st.query_params.clear()
        #                 elif hasattr(st, "experimental_set_query_params"):
        #                     st.experimental_set_query_params()
        #
        #                 time.sleep(0.5)
        #                 st.rerun()
        #         except Exception as e:
        #             st.error("Google sign-in could not be completed. " + cloud_store.safe_db_error(e))
        #
        if st.session_state.auth_view == "Sign In":
            st.subheader("Sign In")
            with st.form("signin_form"):
                login_user = st.text_input("Username")
                login_pass = st.text_input("Password", type="password")
                submit_signin = st.form_submit_button("Sign In")

            if submit_signin:
                if not login_user.strip() or not login_pass:
                    st.error("Enter your username and password, then press Sign In.")
                else:
                    profile = None
                    authenticated = False
                    try:
                        with st.spinner("Signing you in…"):
                            profile = account_database().login(login_user, login_pass)
                        if profile:
                            apply_account(profile)
                            authenticated = True
                        else:
                            st.error("Invalid username or password.")
                    except Exception as error:
                        st.error("Could not sign in. " + cloud_store.safe_db_error(error))
                    if authenticated:
                        st.rerun()

        elif st.session_state.auth_view == "Sign Up":
            st.subheader("Sign Up")
            with st.form("signup_form"):
                new_user = st.text_input("Choose a Username")
                new_pass = st.text_input("Choose a Password", type="password")
                submit_signup = st.form_submit_button("Sign Up")

            if submit_signup:
                if not new_user.strip() or not new_pass:
                    st.error("Enter a username and password, then press Sign Up.")
                else:
                    profile = None
                    authenticated = False
                    try:
                        with st.spinner("Creating your account…"):
                            profile = account_database().signup(new_user, new_pass)
                        apply_account(profile)
                        authenticated = True
                    except ValueError as error:
                        st.error(str(error))
                    except Exception as error:
                        st.error("Could not create your account. " + cloud_store.safe_db_error(error))
                    if authenticated:
                        st.rerun()

# --- STEP 2: FLOWY QUESTIONNAIRE ---
elif not st.session_state.questionnaire_done:
    st.markdown(FLOWY_UI_CSS, unsafe_allow_html=True)

    if st.session_state.get("preference_save_error"):
        st.error(st.session_state.pop("preference_save_error"))
    q_index = st.session_state.current_q
    st.markdown(f'<p class="progress-text">QUESTION {q_index + 1} OF {len(QUESTIONS)}</p>', unsafe_allow_html=True)
    st.markdown(f'<div class="flowy-question">{escape(QUESTIONS[q_index]['text'])}</div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    def handle_answer(answer):
        question = QUESTIONS[q_index]
        option = question["options"][answer]
        answers = list(st.session_state.answers[:q_index])
        answers.append({
            "Question": question["text"], "Answer": option["text"],
            "field": question["field"], "value": option["value"], "choice": answer,
        })
        st.session_state.answers = answers
        st.session_state.current_q += 1
        st.session_state.questionnaire_done = st.session_state.current_q >= len(QUESTIONS)
        st.session_state.pop("audio_plan", None)
        clear_generated_session()
        if st.session_state.questionnaire_done and not st.session_state.guest:
            db = load_db()
            user = db.setdefault(st.session_state.user_email, {})
            user.update(questionnaire_done=True, answers=answers)
            try:
                save_db(db)
            except Exception as error:
                st.session_state.current_q = q_index
                st.session_state.questionnaire_done = False
                st.session_state.answers = answers[:-1]
                st.session_state.preference_save_error = "Preferences could not be saved. " + cloud_store.safe_db_error(error)

    question = QUESTIONS[q_index]
    with col1:
        st.button("A · " + question["options"]["A"]["text"],
                  use_container_width=True, key="answer_a", on_click=handle_answer, args=("A",))
    with col2:
        st.button("B · " + question["options"]["B"]["text"],
                  use_container_width=True, key="answer_b", on_click=handle_answer, args=("B",))
    if q_index > 0 and st.button("← Previous question"):
        st.session_state.current_q -= 1
        st.rerun()
    with st.expander("About these questions"):
        st.caption(INTRO["metadata"]["intro"])
        st.caption(INTRO["metadata"]["disclaimer"])

# --- STEP 3: MAIN APP (Dashboard) ---
else:
    st.markdown("""<style>[data-testid="stAppViewContainer"] { background: rgba(0,0,0,0.4) !important; backdrop-filter: blur(5px); }</style>""", unsafe_allow_html=True)

    profile_css = """
    <style>
    [data-testid="stPopover"] > button {
        background: rgba(0,0,0,0.5) !important; border: 1px solid rgba(255,255,255,0.2) !important;
        border-radius: 50% !important; width: 45px !important; height: 45px !important; padding: 0 !important;
        display: flex; align-items: center; justify-content: center; font-size: 1.2rem !important;
    }
    [data-testid="stPopover"] > button:hover { border: 1px solid white !important; background: rgba(255,255,255,0.2) !important; }
    div[data-testid="stPopoverBody"] {
        background-color: rgba(20, 20, 20, 0.9) !important; backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.2); border-radius: 15px;
        width: min(720px, calc(100vw - 32px)) !important;
        min-width: min(720px, calc(100vw - 32px)) !important;
        max-width: calc(100vw - 32px) !important;
        height: min(560px, calc(100dvh - 240px)) !important;
        max-height: calc(100dvh - 240px) !important;
        overflow-y: auto !important; scrollbar-gutter: stable; box-sizing: border-box;
        overscroll-behavior: contain;
    }
    div[data-testid="stPopoverBody"] div.stButton > button {
        height: auto !important; border-radius: 8px !important;
        background: rgba(255,0,0,0.4) !important; font-size: 1rem !important;
    }
    </style>
    """
    st.markdown(profile_css, unsafe_allow_html=True)

    if st.session_state.get("session_q", 0) >= len(SESSION["items"]):
        head_col1, head_col2 = st.columns([10, 1])

        with head_col1:
            st.title(f"🎉 Dashboard: {st.session_state.username}")

        with head_col2:
            with st.popover("👤"):
                if not st.session_state.get("guest"):
                    st.divider()
                    measurement_ui.settings(load_db, save_db)
                    show_audio_storage(account_database(), st.session_state.get("generated_session"), HERE)
                    if st.session_state.get("measurement_identity") == st.session_state.get("user_email"):
                        with st.expander("My saved session results"):
                            measurement_ui.show_results(in_profile=True)
                    elif st.button("Sign in again for private measurements"):
                        measurement_ui.revoke_player()
                        cm.delete("auth_user")
                        st.session_state.signed_up = False
                        st.session_state.auth_view = "Sign In"
                        st.rerun()

                st.divider()
                if st.button("Log out", use_container_width=True):
                    cm.delete("auth_user")
                    st.session_state.pop("account_database", None)
                    st.session_state.pop("user_id", None)
                    st.session_state.guest = False
                    st.session_state.pop("measurement_identity", None)
                    st.session_state.pop("measurement_opt_in", None)
                    st.session_state.pop("measurement_opt_in_v2", None)
                    st.session_state.show_measurement_results = False
                    st.session_state.session_q = 0
                    st.session_state.session_answers = {}
                    st.session_state.pop("audio_plan", None)
                    clear_generated_session()
                    st.session_state.signed_up = False
                    st.session_state.questionnaire_done = False
                    st.session_state.current_q = 0
                    st.session_state.answers = []
                    st.session_state.username = ""
                    st.session_state.user_email = ""

                    if "token" in st.session_state:
                        del st.session_state["token"]

                    time.sleep(0.5)
                    st.rerun()

        st.write("Create a session around your preferences and the time you have.")
        if st.button("Edit my preferences"):
            st.session_state.questionnaire_done = False
            st.session_state.current_q = 0
            st.session_state.pop("audio_plan", None)
            clear_generated_session()
            st.rerun()

    st.session_state.setdefault("session_q", 0)
    st.session_state.setdefault("session_answers", {})
    preferences = {a["field"]: a["value"] for a in st.session_state.answers}

    def answer_session(choice):
        index = st.session_state.session_q
        question = SESSION["items"][index]
        answers = dict(st.session_state.session_answers)
        answers[question["field"]] = question["options"][choice]["value"]
        st.session_state.session_answers = answers
        st.session_state.session_q = min(index + 1, len(SESSION["items"]))
        st.session_state.pop("audio_plan", None)
        clear_generated_session()

    def previous_session_question():
        st.session_state.session_q = max(0, st.session_state.session_q - 1)
        st.session_state.pop("audio_plan", None)
        clear_generated_session()

    index = st.session_state.session_q
    if index < len(SESSION["items"]):
        st.markdown(FLOWY_UI_CSS, unsafe_allow_html=True)
        question = SESSION["items"][index]
        st.markdown(f'<p class="progress-text">TODAY · QUESTION {index + 1} OF {len(SESSION["items"])}</p>', unsafe_allow_html=True)
        st.markdown(f'<div class="flowy-question">{escape(question["text"])}</div>', unsafe_allow_html=True)
        left, right = st.columns(2)
        with left:
            st.button("A · " + question["options"]["A"]["text"], key="session_answer_a",
                      use_container_width=True, on_click=answer_session, args=("A",))
        with right:
            st.button("B · " + question["options"]["B"]["text"], key="session_answer_b",
                      use_container_width=True, on_click=answer_session, args=("B",))
        if index > 0:
            st.button("← Previous session question", on_click=previous_session_question)
        st.stop()

    st.subheader("Make this time yours")
    st.caption("Choose your session length and narrator.")
    st.markdown("### How many minutes?")
    duration = minute_wheel(st.session_state.get("duration_minutes", 10))
    if duration != st.session_state.get("duration_minutes", 10):
        st.session_state.pop("audio_plan", None)
        clear_generated_session()
    st.session_state.duration_minutes = duration
    narrator = st.selectbox("Narrator", list(VOICE_ENV_KEYS), key="selected_narrator", on_change=update_narrator)
    st.caption("ASMR on uses your narrator's whisper voice. ASMR off uses their normal voice.")
    st.button("← Edit session answers", on_click=previous_session_question)
    current = dict(st.session_state.session_answers)
    submitted = st.button("Prepare audio plan")

    if submitted:
        st.session_state.pop("audio_plan", None)
        clear_generated_session()
        if len(current) != len(SESSION["items"]):
            st.warning("Please answer every session question.")
        elif not any(current.get(k) for k in ("asmr_enabled", "narration_enabled", "nature_enabled")):
            st.warning("No audio is selected. Choose at least one audio layer.")
        else:
            if not current.get("narration_enabled"):
                for field in ("breathing_allowed", "body_awareness_allowed", "muscle_tensing_allowed", "imagery_allowed"):
                    current[field] = False
            if not current.get("body_awareness_allowed"):
                current["muscle_tensing_allowed"] = False
            st.session_state.audio_plan = {
                "status": "ready_to_generate",
                "duration_minutes": duration,
                "voice_choice": narrator if current["narration_enabled"] else None,
                "onboarding_preferences": preferences,
                "session_choices": current,
            }

            if not st.session_state.guest:
                try:
                    plan = st.session_state.audio_plan
                    env_key = VOICE_ENV_KEYS.get(plan.get("voice_choice"))
                    if env_key and current.get("asmr_enabled"):
                        env_key = env_key.removesuffix("_ID") + "_WHISPER_ID"
                    chosen_voice_id = os.getenv(env_key, "").strip() if env_key else ""
                    st.session_state.database_session_id = account_database().start_session(
                        plan, chosen_voice_id, st.session_state.session_answers)
                except Exception as error:
                    st.session_state.pop("audio_plan", None)
                    st.error("Your session choices could not be saved. " + cloud_store.safe_db_error(error))

    if "audio_plan" in st.session_state:
        plan = st.session_state.audio_plan
        st.success("Your session choices are ready.")
        plan_hash = hashlib.sha256(json.dumps({"plan": plan, "script_version": SCRIPT_VERSION}, sort_keys=True).encode()).hexdigest()
        if st.button("Generate my session", key="generate_session"):
            gemini_key = os.getenv("GEMINI_API_KEY", "")
            eleven_key = os.getenv("ELEVENLABS_API_KEY", "")
            voice_env = VOICE_ENV_KEYS.get(plan.get("voice_choice"))
            if voice_env and plan["session_choices"].get("asmr_enabled"):
                voice_env = voice_env.removesuffix("_ID") + "_WHISPER_ID"
            voice_id = os.getenv(voice_env, "").strip() if voice_env else ""
            if not gemini_key or not eleven_key:
                st.error("Add GEMINI_API_KEY and ELEVENLABS_API_KEY to .env.local, then restart the app.")
            elif plan["session_choices"].get("narration_enabled") and not voice_id:
                st.error(f"Add {voice_env} to .env.local for your chosen narrator, then restart the app.")
            else:
                try:
                    with st.spinner(""):
                        if not st.session_state.guest and not st.session_state.get("database_session_id"):
                            st.session_state.database_session_id = account_database().start_session(plan, voice_id, st.session_state.session_answers)
                        if st.session_state.get("script_plan_hash") != plan_hash:
                            script = generate_script(plan, INTRO, gemini_key)
                            st.session_state.generated_script = script
                            st.session_state.script_plan_hash = plan_hash
                        else:
                            script = st.session_state.generated_script
                        voice, background = render_tracks(plan, script, voice_id, eleven_key, progress=lambda _: None)
                        output_folder = HERE / "generated_sessions" / uuid4().hex
                        st.session_state.generated_session = save_session(output_folder, plan, script, voice, background)
                        if not st.session_state.guest:
                            account_database().save_script(st.session_state.database_session_id,
                                                           st.session_state.generated_session, plan, voice_id)
                except GenerationError as error:
                    st.error(str(error))
                except ImportError:
                    st.error("An app dependency is missing. Run python -m pip install -r requirements.txt in the terminal you use to start Streamlit, then restart the app.")
                except OSError:
                    st.error("Could not process or save the audio. Check that the project folder is writable and has free disk space.")
                except Exception as error:
                    st.error(f"Could not finish creating the session ({type(error).__name__}). Please report this error type so it can be diagnosed.")

    if "generated_script" in st.session_state:
        with st.expander("Read your generated script"):
            paragraphs = st.session_state.generated_script["paragraphs"]
            st.write("\n\n".join(paragraphs) if paragraphs else "You chose a session without narration.")

    if "generated_session" in st.session_state:
        session = st.session_state.generated_session
        st.subheader(session["title"])
        st.caption(f"{session['duration_seconds'] // 60} minutes · Voice and background can be muted independently.")
        try:
            monitoring = measurement_ui.player_config(session, load_db)
        except Exception:
            monitoring = None
            st.warning("Session measurements could not start. Audio playback is still available.")
        if hasattr(st, "iframe"):
            st.iframe(player_html(session, monitoring), height=310)
        else:
            components.html(player_html(session, monitoring), height=310, scrolling=False)
        with st.expander("Download your session"):
            for track, label in (("voice", "Download voice"), ("background", "Download background")):
                if session.get(track):
                    st.download_button(label, Path(session[track]).read_bytes(),
                                       file_name=f"{track}.mp3", mime="audio/mpeg", key=f"download_{track}")
            st.download_button("Download script", session["script"], file_name="script.txt", mime="text/plain")
