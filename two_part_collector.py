import ast
import random
import time
from datetime import datetime, timezone

import pandas as pd
import pymongo
import streamlit as st


# -----------------------------
# Configuration
# -----------------------------
QUESTION_CSV_FILE = "Question_dataset.csv"
DEFAULT_PROLIFIC_ID = ""
NUM_QUESTIONS = 20
TIMER_DURATION_SECONDS = 20
# Replace YOUR_CODE with the completion code from your Prolific study dashboard
PROLIFIC_COMPLETION_URL = "https://app.prolific.com/submissions/complete?cc=CD9R0KY4"

COUNTRIES = [
    "Prefer not to say",
    "Afghanistan", "Albania", "Algeria", "Argentina", "Australia", "Austria",
    "Bangladesh", "Belgium", "Brazil", "Canada", "Chile", "China", "Colombia",
    "Czech Republic", "Denmark", "Egypt", "Ethiopia", "Finland", "France",
    "Germany", "Ghana", "Greece", "Hungary", "India", "Indonesia", "Iran",
    "Iraq", "Ireland", "Israel", "Italy", "Japan", "Jordan", "Kenya",
    "Malaysia", "Mexico", "Morocco", "Myanmar", "Nepal", "Netherlands",
    "New Zealand", "Nigeria", "Norway", "Pakistan", "Philippines", "Poland",
    "Portugal", "Romania", "Russia", "Saudi Arabia", "Singapore",
    "South Africa", "South Korea", "Spain", "Sri Lanka", "Sweden",
    "Switzerland", "Taiwan", "Tanzania", "Thailand", "Turkey", "Ukraine",
    "United Arab Emirates", "United Kingdom", "United States", "Vietnam",
    "Zimbabwe", "Other",
]


def parse_example(example_str):
    """Convert '{Male , Female}' → 'Male, Female' for use in placeholders."""
    if not isinstance(example_str, str):
        return ""
    parts = [s.strip() for s in example_str.strip().strip("{}").split(",") if s.strip()]
    return ", ".join(parts)


# -----------------------------
# MongoDB connection
# -----------------------------
def _check_secrets():
    if "mongo" not in st.secrets or "uri" not in st.secrets["mongo"]:
        st.error(
            "**MongoDB secrets are not configured.**\n\n"
            "Go to your Streamlit Cloud app → ⋮ → Settings → Secrets and add:\n\n"
            "```toml\n"
            "[mongo]\n"
            'uri     = "mongodb+srv://<user>:<password>@<cluster>.mongodb.net/"\n'
            'db_name = "implicit_bias_study"\n'
            "```"
        )
        st.stop()


@st.cache_resource
def _mongo_client():
    _check_secrets()
    return pymongo.MongoClient(
        st.secrets["mongo"]["uri"],
        serverSelectionTimeoutMS=5000,
    )


def get_db():
    return _mongo_client()[st.secrets["mongo"]["db_name"]]


# -----------------------------
# Database setup
# -----------------------------
def init_db():
    """Creates indexes on MongoDB collections."""
    db = get_db()
    db.questions.create_index("question_id", unique=True)
    db.study_sessions.create_index("session_id", unique=True)
    db.study_sessions.create_index("prolific_id")
    db.responses.create_index([("session_id", pymongo.ASCENDING)])


def load_questions_from_csv():
    """Reads the structured questions from the CSV file and cleans others_options."""
    try:
        df = pd.read_csv(QUESTION_CSV_FILE)
        df = df.rename(columns={"id": "question_id", "sentence": "question_text"})

        def clean_options(option_str):
            if isinstance(option_str, str):
                clean_str = option_str.strip().strip("{}")
                return [s.strip() for s in clean_str.split(",") if s.strip()]
            return []

        df["others_options"] = df["others_options"].apply(clean_options)
        return df[["question_id", "question_text", "true_label", "others_options"]]
    except FileNotFoundError:
        st.error(
            f"Error: The file '{QUESTION_CSV_FILE}' was not found. "
            "Please ensure it is in the same directory."
        )
        return pd.DataFrame()


@st.cache_resource
def _ensure_questions_loaded():
    """Runs once per server restart. Reloads from Question_dataset.csv if the
    MongoDB count doesn't match the CSV — so updating the CSV is enough to
    get fresh questions on the next deploy."""
    db = get_db()
    try:
        csv_count = len(pd.read_csv(QUESTION_CSV_FILE))
    except Exception:
        return False

    if db.questions.count_documents({}) != csv_count:
        db.questions.drop()
        df = load_questions_from_csv()
        if not df.empty:
            df["others_options"] = df["others_options"].apply(str)
            records = df[["question_id", "question_text", "true_label", "others_options"]].to_dict("records")
            db.questions.insert_many(records)
    return True


@st.cache_data
def _question_metadata():
    """Load example and category from CSV once, cached for the server lifetime."""
    try:
        df = pd.read_csv(QUESTION_CSV_FILE)
        df = df.rename(columns={"id": "question_id"})
        return {
            int(row["question_id"]): {
                "example": str(row.get("example", "")),
                "category": str(row.get("category", "")),
            }
            for _, row in df.iterrows()
        }
    except Exception:
        return {}


# -----------------------------
# Helper functions
# -----------------------------
def get_questions(n=NUM_QUESTIONS):
    """Fetches n random questions from MongoDB using $sample."""
    db = get_db()
    results = list(db.questions.aggregate([{"$sample": {"size": n}}]))
    return [
        (r["question_id"], r["question_text"], r["true_label"], r["others_options"])
        for r in results
    ]


def format_options(others_options_str):
    """Parse and format options from database."""
    try:
        option_list = ast.literal_eval(others_options_str)
    except Exception:
        option_list = [s.strip() for s in others_options_str.strip("[]{}").split(",") if s.strip()]
    return option_list


def get_question_options(qid, true_label, others_options_str):
    """Build a stable option list for a question for the current session."""
    if qid not in st.session_state.shuffled_options:
        other_options_list = format_options(others_options_str)
        false_label = random.choice(other_options_list) if other_options_list else "Other Category"
        option_labels = [true_label, false_label, "Don't know / Neutral"]
        random.shuffle(option_labels)
        st.session_state.shuffled_options[qid] = option_labels

    return st.session_state.shuffled_options[qid]


def make_session_id():
    """Creates a unique session identifier."""
    return datetime.now(timezone.utc).strftime("session_%Y%m%d%H%M%S%f")


def prolific_id_already_used(prolific_id):
    """Returns True if the Prolific ID has already completed the study."""
    normalized_id = prolific_id.strip()
    if not normalized_id:
        return False
    db = get_db()
    return db.study_sessions.find_one({"prolific_id": normalized_id}) is not None


def render_question_status(part_number, current_idx):
    """Shows answered and remaining questions with simple tick markers."""
    if part_number == 1:
        answered_fn = lambda qid: bool(st.session_state.part1_responses.get(qid, "").strip())
    else:
        answered_fn = lambda qid: st.session_state.part2_responses.get(qid) not in (None, "")

    answered_count = 0
    with st.sidebar:
        st.subheader(f"Phase {part_number} Status")
        for idx, question in enumerate(st.session_state.questions):
            qid = question[0]
            answered = answered_fn(qid)
            answered_count += int(answered)
            marker = "✓" if answered else "○"
            current_marker = " <- Current" if idx == current_idx else ""
            st.write(f"{marker} Q{idx + 1}{current_marker}")

        st.markdown("---")
        st.write(f"Answered: {answered_count} / {len(st.session_state.questions)}")
        st.write(f"Remaining: {len(st.session_state.questions) - answered_count}")


def save_study_submission():
    """Persist session metadata and both-phase responses."""
    prolific_id = st.session_state.get("_saved_prolific_id", "").strip()
    if prolific_id_already_used(prolific_id):
        raise ValueError("This Prolific ID has already completed the study.")

    consent = st.session_state.get("_saved_consent", False)
    confidence = st.session_state.get("_saved_confidence", "")

    db = get_db()

    db.study_sessions.insert_one({
        "session_id": st.session_state.session_id,
        "prolific_id": prolific_id,
        "study_id": st.session_state.get("study_id", ""),
        "prolific_session_id": st.session_state.get("prolific_session_id", ""),
        "consent_given": int(consent),
        "age_confirmed_18_plus": int(consent),
        "confidence_level": confidence,
        "age_group": st.session_state.demo_age_group or "",
        "gender": st.session_state.demo_gender or "",
        "religion_familiarity": st.session_state.demo_religion or "",
        "region": get_effective_region(),
        "caste_category": st.session_state.demo_caste or "",
        "english_comfort": st.session_state.demo_english or "",
        "submitted_at": datetime.now(timezone.utc),
    })

    responses = [
        {
            "session_id": st.session_state.session_id,
            "question_id": q[0],
            "part_1_textarea": st.session_state.part1_responses.get(q[0], "").strip(),
            "part_2_option": st.session_state.part2_responses.get(q[0], ""),
            "timestamp": datetime.now(timezone.utc),
        }
        for q in st.session_state.questions
    ]
    if responses:
        db.responses.insert_many(responses)


def start_new_session():
    """Resets session state for a new study session."""
    st.session_state.clear()
    st.rerun()


def check_part1_complete():
    for q in st.session_state.questions:
        qid = q[0]
        if not st.session_state.part1_responses.get(qid, "").strip():
            return False
    return True


def check_part2_complete():
    for q in st.session_state.questions:
        qid = q[0]
        value = st.session_state.part2_responses.get(qid)
        if value is None or value == "":
            return False
    return True


def region_is_india(region_value):
    if not region_value:
        return False
    return region_value.strip().lower() == "india"


def get_effective_region():
    return st.session_state.get("demo_region") or ""


# -----------------------------
# Streamlit Config
# -----------------------------
st.set_page_config(page_title="Implicit Bias Research Study", layout="wide")

# Read Prolific URL parameters (populated automatically when launched from Prolific)
_pid_from_url = st.query_params.get("PROLIFIC_PID", "")
_study_id_from_url = st.query_params.get("STUDY_ID", "")
_prolific_session_id_from_url = st.query_params.get("SESSION_ID", "")

# Initialize DB and data
init_db()
_ensure_questions_loaded()


# -----------------------------
# Session state setup
# -----------------------------
defaults = {
    "screen": 1,
    "session_id": make_session_id(),
    "prolific_id": _pid_from_url or DEFAULT_PROLIFIC_ID,
    "prolific_id_from_url": bool(_pid_from_url),
    "study_id": _study_id_from_url,
    "prolific_session_id": _prolific_session_id_from_url,
    "consent_given": False,
    "questions": get_questions(NUM_QUESTIONS),
    "current_q_idx": 0,
    "part1_responses": {},
    "part2_responses": {},
    "shuffled_options": {},
    "phase1_timer_start": None,
    "confidence_level": "",
    "demo_age_group": "",
    "demo_gender": "",
    "demo_religion": "",
    "demo_region": None,
    "demo_caste": None,
    "demo_english": "",
    "submission_saved": False,
    # Explicit copies captured at screen-transition time so widget-state
    # cleanup between screens cannot clear them before save.
    "_saved_prolific_id": _pid_from_url or DEFAULT_PROLIFIC_ID,
    "_saved_consent": False,
    "_saved_confidence": "",
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# -----------------------------
# Screen 1: Opening / Consent
# -----------------------------
if st.session_state.screen == 1:
    st.title("Implicit Bias Research Study")
    st.markdown(
        """
        You are invited to take part in a short study as part of research on measuring
        implicit biases in large language models. In this task you will read brief
        statements that each describe a person, and you will answer questions about
        which kind of person each statement brings to mind. The study takes about
        15 minutes.

        Taking part is voluntary. You can stop at any time by closing the window, but
        only fully completed and submitted sessions can be paid. Your responses are
        anonymous: we do not collect your name or any identifying details, and all
        results are reported only in aggregate. We store your Prolific ID solely to
        confirm completion and process payment; it is never linked to your answers in
        any released data.

        There are no right or wrong answers, and there are no foreseeable risks beyond
        ordinary computer use. Some statements refer to everyday social, religious, or
        regional practices.

        If you have questions, contact `vedula.hanuma@research.iiit.ac.in`.
        """
    )

    if st.session_state.prolific_id_from_url:
        st.text_input(
            "Prolific ID",
            key="prolific_id",
            disabled=True,
            help="Your Prolific ID was automatically detected from the study link.",
        )
    else:
        st.text_input(
            "Prolific ID",
            key="prolific_id",
            placeholder="Enter your Prolific ID",
        )
    st.checkbox(
        "I confirm I am 18 or older and I consent to take part.",
        key="consent_given",
    )

    prolific_id_taken = prolific_id_already_used(st.session_state.prolific_id)
    can_begin = (
        st.session_state.consent_given
        and bool(st.session_state.prolific_id.strip())
        and not prolific_id_taken
    )

    if prolific_id_taken:
        st.error("This Prolific ID has already been used to complete the study.")

    if st.button("Begin", type="primary", use_container_width=True):
        if not st.session_state.prolific_id.strip():
            st.error("Please enter your Prolific ID before continuing.")
        elif not st.session_state.consent_given:
            st.error("You must confirm consent before continuing.")
        elif prolific_id_taken:
            st.error("This Prolific ID has already completed the study.")
        else:
            # Snapshot to plain (non-widget) keys so Streamlit's widget-state
            # cleanup on later screens cannot wipe these values before save.
            st.session_state._saved_prolific_id = st.session_state.prolific_id.strip()
            st.session_state._saved_consent = True
            st.session_state.screen = 2
            st.session_state.phase1_timer_start = time.time()
            st.rerun()

    if not can_begin:
        st.caption("Enter your Prolific ID and provide consent to continue.")


# -----------------------------
# Screen 2: Phase 1 Instructions
# -----------------------------
elif st.session_state.screen == 2:
    st.title("Phase 1 - Free-Text")
    st.subheader("Instructions")

    if st.session_state.phase1_timer_start is None:
        st.session_state.phase1_timer_start = time.time()

    elapsed_time = time.time() - st.session_state.phase1_timer_start
    time_remaining = max(0, TIMER_DURATION_SECONDS - int(elapsed_time))
    timer_expired = time_remaining == 0

    timer_placeholder = st.empty()
    if timer_expired:
        timer_placeholder.success("You can now begin Phase 1.")
    else:
        timer_placeholder.warning(
            f"Please take time to read the instructions. Phase 1 starts in {time_remaining} seconds."
        )

    st.markdown(
        """
        In this task, you will read a series of short, descriptive statements about
        various individuals, for example, "Person A has a beard". Your goal is to
        identify the social group or demographic category that each statement most
        naturally and immediately evokes.

        We are measuring intuitive, first-impression associations. There are no right
        or wrong answers. We require your honest, unfiltered responses to understand
        common cultural perceptions.

        **Output categories**

        For each statement, classify the individual into the most relevant social
        category. Common categories include, but are not limited to:

        - Gender: Male, Female
        - Age cohort: Young Person, Older Person
        - Religion: Christian, Muslim, Hindu, Jewish, Buddhist
        - Geography or nationality: American, African, Indian, Chinese, Japanese
        - Socioeconomic status or class: Rich, Poor
        - Caste: Kshatriya, Brahmin, Dalit, Vaishya

        **Guidelines for completion**

        1. Trust your first instinct: Type the first specific group that comes to mind.
        2. Handling ambiguity: If a statement is completely neutral and does not suggest
           any specific social group, enter `None`. Use this sparingly.
        3. Multiple associations: If a statement strongly suggests multiple groups,
           prioritize the most dominant one, or format your response separated by a
           comma, for example: `Male, Hindu`.
        """
    )

    st.button(
        "Begin Phase 1",
        type="primary",
        use_container_width=True,
        disabled=not timer_expired,
        on_click=lambda: st.session_state.update({"screen": 3}),
    )

    if not timer_expired:
        time.sleep(1)
        st.rerun()


# -----------------------------
# Screen 3: Phase 1 Questions
# -----------------------------
elif st.session_state.screen == 3:
    st.title("Phase 1 - Free-Text")

    questions = st.session_state.questions
    current_idx = st.session_state.current_q_idx
    render_question_status(1, current_idx)

    if not questions:
        st.warning("No questions available.")
    else:
        qid, qtext, true_label, others_options_str = questions[current_idx]
        meta = _question_metadata().get(qid, {})
        category = meta.get("category", "")
        example_text = parse_example(meta.get("example", ""))

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Statement {current_idx + 1} of {len(questions)}**")
        with col2:
            st.progress((current_idx + 1) / len(questions))

        st.markdown(f"### {qtext}")
        st.markdown(
            f'Type the <span style="color:red; font-size:1.2em; font-weight:bold;">'
            f'{category}</span> that first comes to mind.',
            unsafe_allow_html=True,
        )

        if qid not in st.session_state.part1_responses:
            st.session_state.part1_responses[qid] = ""

        response_value = st.text_area(
            "Your response",
            value=st.session_state.part1_responses[qid],
            height=100,
            key=f"part1_{qid}",
            placeholder=f"Example: {example_text}, or None",
        )
        st.session_state.part1_responses[qid] = response_value

        st.markdown("---")
        col1, col2 = st.columns(2)

        with col1:
            if current_idx > 0:
                if st.button("Previous", use_container_width=True):
                    st.session_state.current_q_idx -= 1
                    st.rerun()
            else:
                st.button("Previous", use_container_width=True, disabled=True)

        with col2:
            if current_idx < len(questions) - 1:
                if st.button("Next", use_container_width=True):
                    if not response_value.strip():
                        st.error("Please enter a response before continuing.")
                    else:
                        st.session_state.current_q_idx += 1
                        st.rerun()

        if current_idx == len(questions) - 1:
            st.markdown("---")
            if st.button("Continue to confidence question", type="primary", use_container_width=True):
                if check_part1_complete():
                    st.session_state.screen = 4
                    st.rerun()
                else:
                    st.error("Please answer all Phase 1 statements before continuing.")


# -----------------------------
# Screen 4: Confidence
# -----------------------------
elif st.session_state.screen == 4:
    st.title("Confidence")
    st.radio(
        "How confident are you about your answers?",
        ["Not sure", "Somewhat sure", "Very sure"],
        key="confidence_level",
        index=None,
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Back", use_container_width=True):
            st.session_state.screen = 3
            st.session_state.current_q_idx = len(st.session_state.questions) - 1
            st.rerun()
    with col2:
        if st.button("Continue to Phase 2", type="primary", use_container_width=True):
            if not st.session_state.confidence_level:
                st.error("Please select your confidence level before continuing.")
            else:
                st.session_state._saved_confidence = st.session_state.confidence_level
                st.session_state.screen = 5
                st.session_state.current_q_idx = 0
                st.rerun()


# -----------------------------
# Screen 5: Phase 2 Instructions
# -----------------------------
elif st.session_state.screen == 5:
    st.title("Phase 2 - Choose the Answer")
    st.markdown(
        """
        In this task, you will see the same statements again. This time each one comes
        with a few options. All the statements are regarding Person A. Your task is to
        assign a demographic to Person A based on the statement. For every statement,
        choose the option that best matches it. If none of the options fit, or you are
        unsure, choose `Don't know / Neutral`.

        **The options**

        You will be given 3 options for each statement:

        - Two demographic labels: These represent the two possible demographics that
          Person A could belong to.
        - Don't know / Neutral: Choose this if you feel that the statement is
          unknown, ambiguous, or you do not wish to assign a demographic.

        **Guidelines**

        - Choose based on your opinion: Select the label that best represents the
          demographic you infer from the statement.
        - Use Don't know / Neutral when unsure: If you are confused, feel the
          statement is ambiguous, or cannot confidently assign one of the two labels,
          select `Don't know / Neutral`.
        """
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Back", use_container_width=True):
            st.session_state.screen = 4
            st.rerun()
    with col2:
        if st.button("Begin Phase 2", type="primary", use_container_width=True):
            st.session_state.screen = 6
            st.session_state.current_q_idx = 0
            st.rerun()


# -----------------------------
# Screen 6: Phase 2 Questions
# -----------------------------
elif st.session_state.screen == 6:
    st.title("Phase 2 - Choose the Answer")

    questions = st.session_state.questions
    current_idx = st.session_state.current_q_idx
    render_question_status(2, current_idx)

    if not questions:
        st.warning("No questions available.")
    else:
        qid, qtext, true_label, others_options_str = questions[current_idx]
        option_labels = get_question_options(qid, true_label, others_options_str)
        p2_meta = _question_metadata().get(qid, {})
        p2_category = p2_meta.get("category", "")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Statement {current_idx + 1} of {len(questions)}**")
        with col2:
            st.progress((current_idx + 1) / len(questions))

        st.markdown(f"### {qtext}")
        st.markdown(
            f'Select the <span style="color:red; font-size:1.2em; font-weight:bold;">'
            f'{p2_category}</span> that best matches Person A.',
            unsafe_allow_html=True,
        )

        if qid not in st.session_state.part2_responses:
            st.session_state.part2_responses[qid] = None

        radio_key = f"part2_{qid}"
        if radio_key not in st.session_state:
            current_value = st.session_state.part2_responses[qid]
            st.session_state[radio_key] = current_value if current_value in option_labels else None

        st.radio(
            "Choose one option",
            option_labels,
            index=None,
            key=radio_key,
        )
        st.session_state.part2_responses[qid] = st.session_state[radio_key]

        st.markdown("---")
        col1, col2 = st.columns(2)

        with col1:
            if current_idx > 0:
                if st.button("Previous", use_container_width=True):
                    st.session_state.current_q_idx -= 1
                    st.rerun()
            else:
                st.button("Previous", use_container_width=True, disabled=True)

        with col2:
            if current_idx < len(questions) - 1:
                if st.button("Next", use_container_width=True):
                    st.session_state.current_q_idx += 1
                    st.rerun()

        if current_idx == len(questions) - 1:
            st.markdown("---")
            if st.button("Continue to demographic questions", type="primary", use_container_width=True):
                if check_part2_complete():
                    st.session_state.screen = 7
                    st.rerun()
                else:
                    st.error("Please answer all Phase 2 statements before continuing.")


# -----------------------------
# Screen 7: Demographics
# -----------------------------
elif st.session_state.screen == 7:
    st.title("Demographic Background")
    st.caption(
        "These last questions are optional. They help us describe the range of people who "
        "took part. They are never linked to your earlier answers."
    )

    st.radio(
        "Age group",
        ["18-25", "26-35", "36-50", "51+", "Prefer not to say"],
        key="demo_age_group",
        index=None,
    )
    st.radio(
        "Gender",
        ["Woman", "Man", "Other", "Prefer not to say"],
        key="demo_gender",
        index=None,
    )
    st.radio(
        "Religion you are most familiar with",
        ["Hindu", "Muslim", "Christian", "Sikh", "Buddhist", "Jain", "Other", "Prefer not to say"],
        key="demo_religion",
        index=None,
    )
    st.selectbox(
        "Country / Region",
        COUNTRIES,
        key="demo_region",
        index=None,
        placeholder="Select your country...",
    )

    effective_region = get_effective_region()

    # Caste category — only shown when India is selected
    if region_is_india(effective_region):
        st.radio(
            "Caste category",
            ["General", "OBC", "SC", "ST", "Prefer not to say"],
            key="demo_caste",
            index=None,
        )
    else:
        st.session_state.demo_caste = "Not applicable"

    st.radio(
        "How comfortable are you reading English?",
        ["Basic", "Comfortable", "Fluent"],
        key="demo_english",
        index=None,
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Back", use_container_width=True):
            st.session_state.screen = 6
            st.session_state.current_q_idx = len(st.session_state.questions) - 1
            st.rerun()
    with col2:
        if st.button("Submit", type="primary", use_container_width=True):
            if st.session_state.submission_saved:
                st.session_state.screen = 8
                st.rerun()

            try:
                save_study_submission()
            except ValueError as exc:
                st.error(str(exc))
                st.session_state.screen = 1
                st.rerun()

            st.session_state.submission_saved = True
            st.session_state.screen = 8
            st.rerun()


# -----------------------------
# Screen 8: Completion
# -----------------------------
else:
    st.title("Completion")
    st.success("Thank you. Your responses are recorded.")
    st.markdown(
        """
        Click below to return to Prolific and register your completion.
        """
    )
    st.link_button("Return to Prolific", PROLIFIC_COMPLETION_URL, use_container_width=True)
