import streamlit as st
import sqlite3
import random
import os
import pandas as pd 
import ast 
from datetime import datetime
import time 

# -----------------------------
# Configuration
# -----------------------------
DB_NAME = "responses.db"
QUESTION_CSV_FILE = "Question_dataset.csv" 
DEFAULT_USER_NAME = "Annotator_Guest" 
TIMER_DURATION_SECONDS = 20 # Minimum timer duration

# -----------------------------
# Database setup (UNCHANGED)
# -----------------------------

def init_db():
    """Initializes the SQLite database with necessary tables."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Table for questions 
    c.execute('''CREATE TABLE IF NOT EXISTS questions (
                question_id INTEGER PRIMARY KEY,
                question_text TEXT,
                true_label TEXT,
                others_options TEXT
            )''')
    
    # Table for responses - allows any text label (Male, Female, Neutral, etc.)
    c.execute('''CREATE TABLE IF NOT EXISTS responses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT,
                question_id INTEGER,
                response TEXT, 
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )''')
    conn.commit()
    conn.close()

def load_questions_from_csv():
    """Reads the structured questions from the CSV file and cleans others_options."""
    try:
        df = pd.read_csv(QUESTION_CSV_FILE)
        df = df.rename(columns={'id': 'question_id', 'sentence': 'question_text'})
        
        def clean_options(option_str):
            if isinstance(option_str, str):
                clean_str = option_str.strip().strip('{}')
                return [s.strip() for s in clean_str.split(',')]
            return []
            
        df['others_options'] = df['others_options'].apply(clean_options)
        
        return df[['question_id', 'question_text', 'true_label', 'others_options']]
    except FileNotFoundError:
        st.error(f"Error: The file '{QUESTION_CSV_FILE}' was not found. Please ensure it is in the same directory.")
        return pd.DataFrame() 

def insert_questions_if_empty():
    """Inserts data from the CSV into the questions table if it's empty."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM questions")
    count = c.fetchone()[0]
    
    if count == 0:
        st.info(f"Initializing database from {QUESTION_CSV_FILE}...")
        df_questions = load_questions_from_csv()
        
        if not df_questions.empty:
            df_questions['others_options_str'] = df_questions['others_options'].apply(str)
            
            data_to_insert = df_questions[['question_id', 'question_text', 'true_label', 'others_options_str']].values.tolist()
            
            c.executemany("""
                INSERT INTO questions (question_id, question_text, true_label, others_options) 
                VALUES (?, ?, ?, ?)
            """, data_to_insert)
            
            conn.commit()
            st.success(f"Successfully loaded {len(df_questions)} questions into the database.")
        else:
            st.warning("Could not load data from CSV. The 'questions' table remains empty. Cannot start assessment.")
            
    conn.close()


# -----------------------------
# Helper functions
# -----------------------------
def get_random_questions(user_name, n=20):
    """
    Fetches n random questions from the entire pool, ignoring past user responses, 
    to allow for multiple annotations per question.
    """
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Fetch all questions from the database
    c.execute("SELECT question_id, question_text, true_label, others_options FROM questions")
    all_questions = c.fetchall()
    conn.close()
    
    if not all_questions:
        # If the questions table is empty
        return []

    # Return a random sample of n questions (or fewer if less than n are available)
    if len(all_questions) < n:
        return all_questions
        
    return random.sample(all_questions, n)

def save_response(user_name, question_id, response):
    """Saves a single user response (the selected label) to the database."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""
        INSERT INTO responses (user_name, question_id, response) 
        VALUES (?, ?, ?)
    """, (user_name, question_id, response))
    conn.commit()
    conn.close()

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Identify the Demographic", layout="centered")

# Initialize DB and data
init_db()
insert_questions_if_empty()

# -----------------------------
# Session state setup
# -----------------------------
if "user_name" not in st.session_state:
    st.session_state.user_name = DEFAULT_USER_NAME
if "questions" not in st.session_state or not st.session_state.questions:
    st.session_state.questions = get_random_questions(st.session_state.user_name, 20)
if "current_idx" not in st.session_state:
    st.session_state.current_idx = 0
if "responses" not in st.session_state:
    st.session_state.responses = {}
if "instructions_shown" not in st.session_state:
    st.session_state.instructions_shown = False
# Timer state variables
if "timer_start_time" not in st.session_state:
    st.session_state.timer_start_time = None
if "assessment_started" not in st.session_state:
    st.session_state.assessment_started = False


def start_new_session():
    """Clears session state and resets for a new session."""
    # This function is now only used as a clean way to exit/restart the app
    # after the assessment is fully complete.
    st.session_state.clear()
    st.session_state.user_name = DEFAULT_USER_NAME
    st.session_state.timer_start_time = None
    st.session_state.assessment_started = False 
    st.session_state.questions = get_random_questions(st.session_state.user_name, 20)
    st.session_state.current_idx = 0
    st.session_state.responses = {}
    st.rerun() 

def start_assessment_button_handler():
    """Sets the state to allow question display."""
    st.session_state.assessment_started = True


# --- Core logic to handle the button click and advance question ---
def handle_answer_submission(response_value):
    """Saves the answer and updates the state."""
    if st.session_state.current_idx < len(st.session_state.questions):
        qid = st.session_state.questions[st.session_state.current_idx][0]
        save_response(st.session_state.user_name, qid, response_value)
        st.session_state.responses[qid] = response_value
        st.session_state.current_idx += 1
# -------------------------------------------------------------------


# -----------------------------
# Main Application Flow
# -----------------------------

# -----------------------------
# Instructions and Timer Section (Conditional Display)
# -----------------------------

if not st.session_state.assessment_started:
    
    # --- HEADING DISPLAYED ONLY ON FIRST PAGE ---
    st.title("🧠 Identify the Demographic")
    st.markdown("---")
    
    # 1. Set the timer start time on first load
    if st.session_state.timer_start_time is None:
        st.session_state.timer_start_time = time.time()
        
    # 2. Calculate elapsed time
    elapsed_time = time.time() - st.session_state.timer_start_time
    time_remaining = max(0, TIMER_DURATION_SECONDS - int(elapsed_time))
    timer_expired = time_remaining == 0
    
    # 3. Display instructions
    st.subheader("Task Instructions (Read Carefully!)")
    
    # Placeholder for the timer/status display
    timer_placeholder = st.empty() 
    
    if not timer_expired:
        timer_placeholder.warning(f"⏰ Please take a moment to read the instructions. Assessment is enabled in: **{time_remaining} seconds**")
    else:
        timer_placeholder.success("✅ Instructions read time complete. You may now start the assessment.")

    st.markdown("""
    All the statements are regarding **Person A**. Your task is to assign a demographic to Person A based on the statement. 
    
    **Please note that sentences regarding Person A can be from different demographics. Demographics can include:** **Gender, Religion, Region, Caste, Age, Socio-economic status, Race, and Disability.**

    ### The Options
    You will be given **3 options** for each statement:
    * **Two Demographic Labels**: These represent the two possible demographics that Person A could belong to (e.g., 'Male' and 'Female', or 'Asian' and 'Caucasian').
    * **Don't know/Neutral**: Choose this if you feel that the statement is **unknown/ambiguous** or if you **do not wish to assign a demographic** based on the statement.
    
    ### Guidelines
    * **Choose based on your opinion**: Select the label that best represents the demographic you infer from the statement.
    * **Use Don't know/Neutral when unsure**: If you are confused, feel the statement is ambiguous, or cannot confidently assign one of the two demographic labels, select **Don't know/Neutral**.
    * **Search if needed**: If you are unfamiliar with a word or the context of the sentence, feel free to use a **Google search** to clarify your understanding before making a selection.
    
    """)
    st.markdown("---")
    
    # 4. START BUTTON LOGIC
    st.button(
        "Start Assessment", 
        type="primary", 
        disabled=not timer_expired, 
        on_click=start_assessment_button_handler
    )

    # 5. Force a rerun to update the timer every second if not expired
    if not timer_expired:
        time.sleep(1)
        st.rerun()

# -----------------------------
# Assessment Running (Only runs if assessment_started is True)
# -----------------------------
else: # if st.session_state.assessment_started is True
    
    idx = st.session_state.current_idx
    questions = st.session_state.questions

    if not questions:
        st.warning("No questions are available in the database. Please check the `Question_dataset.csv` file.")

    elif idx < len(questions):
        st.sidebar.markdown(f"**Current Session:** `{st.session_state.user_name}`")
        
        qid, qtext, true_label, others_options_str = questions[idx]

        # --- DYNAMIC OPTION GENERATION ---
        try:
            other_options_list = ast.literal_eval(others_options_str)
        except:
            other_options_list = [s.strip() for s in others_options_str.strip('[]{}').split(',')]

        if other_options_list and len(other_options_list) > 0:
            false_label = random.choice(other_options_list)
        else:
            false_label = "Other Category" 

        option_labels = [true_label, false_label, "Don't know/Neutral"]
        random.shuffle(option_labels)
        
        # ---------------------------------
        
        st.markdown(f"## Question {idx+1} of {len(questions)}")
        st.progress((idx+1)/len(questions))
        
        # Display question card
        with st.container(border=True):
            st.subheader(qtext)
            st.markdown("---")
            st.markdown('<p style="font-size: 16px;"><b>Select the demographic label for Person A:</b></p>', unsafe_allow_html=True) 

        # --- Response Buttons ---
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.button(
                option_labels[0], 
                use_container_width=True, 
                on_click=handle_answer_submission, 
                args=(option_labels[0],),
                type="secondary"
            )

        with col2:
            st.button(
                option_labels[1], 
                use_container_width=True, 
                on_click=handle_answer_submission, 
                args=(option_labels[1],),
                type="secondary"
            )

        with col3:
            st.button(
                option_labels[2], 
                use_container_width=True, 
                on_click=handle_answer_submission, 
                args=(option_labels[2],),
                type="secondary"
            )


    else:
        # --- Completion Screen ---
        st.balloons()
        st.success("🎉 Assessment Complete!")
        
        st.markdown("Your responses are **invaluable** to us and to the community. **Cheers to you for helping us build Responsible and Safe AI systems!** 🤝")
        st.markdown("---")
        
        unanswered_count = len(get_random_questions(st.session_state.user_name, 10000))
        
        # --- REMOVED THE "START NEXT ROUND" LOGIC ---
        if unanswered_count > 0:
            st.write("Thank you for completing this questions.")
        
        else:
            st.write("Thank you for your hard work.")
            st.success("You have successfully answered all available questions in the database!")
            
        # Provide a final exit/restart option
        st.write("If you want annotate more sentences.")
        st.button("Start New Session (Re-load)", on_click=start_new_session, type="primary")