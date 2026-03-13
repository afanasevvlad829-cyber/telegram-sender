import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from urllib.parse import quote

DEFAULT_WORKSHEET_NAME = "outreach"

TEMPLATES = [
    "Здравствуйте, {name}! Пишу вам по теме: {topic}. Если актуально, могу коротко прислать подробности.",
    "{name}, добрый день! Пишу насчёт темы «{topic}». Если интересно, могу отправить краткую информацию.",
    "Здравствуйте, {name}! Увидел интерес к теме «{topic}». Если хотите, пришлю детали."
]


def normalize(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def template_for(index: int) -> str:
    return TEMPLATES[index % len(TEMPLATES)]


def build_message(row: dict, index: int) -> str:
    existing = normalize(row.get("message"))
    if existing:
        return existing

    name = normalize(row.get("name")) or "Здравствуйте"
    topic = normalize(row.get("topic")) or "ваш запрос"
    return template_for(index).format(name=name, topic=topic)


def dialog_url(row: dict) -> str:
    telegram = normalize(row.get("telegram")).replace("@", "")
    phone = normalize(row.get("phone"))

    if telegram:
        return f"https://t.me/{telegram}"

    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if digits:
            return f"https://t.me/+{digits}"

    return ""


def share_url(dialog: str, message: str) -> str:
    if not dialog:
        return ""
    return f"{dialog}?text={quote(message)}"


def get_credentials_from_secrets() -> Credentials:
    if "gcp_service_account" not in st.secrets:
        raise RuntimeError(
            "В secrets не найден блок gcp_service_account. Добавь service account в Streamlit secrets."
        )

    info = dict(st.secrets["gcp_service_account"])
    if "private_key" in info:
        info["private_key"] = str(info["private_key"]).replace("\\n", "\n")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)


@st.cache_resource
def get_gspread_client():
    creds = get_credentials_from_secrets()
    return gspread.authorize(creds)


@st.cache_data(ttl=30)
def load_sheet(spreadsheet_url: str, worksheet_name: str) -> pd.DataFrame:
    client = get_gspread_client()
    sheet = client.open_by_url(spreadsheet_url)
    ws = sheet.worksheet(worksheet_name)
    data = ws.get_all_records()
    return pd.DataFrame(data)


def ensure_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "name",
        "phone",
        "telegram",
        "topic",
        "message",
    ]
    for col in required:
        if col not in df.columns:
            df[col] = ""
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = ensure_required_columns(df.copy())
    rows = []

    for i, row in enumerate(df.fillna("").to_dict("records")):
        message = build_message(row, i)
        durl = dialog_url(row)

        rows.append({
            "name": normalize(row.get("name")),
            "phone": normalize(row.get("phone")),
            "telegram": normalize(row.get("telegram")),
            "message": message,
            "dialog": durl,
            "share": share_url(durl, message),
            "sent_local": False,
        })

    result = pd.DataFrame(rows)
    for col in ["name", "phone", "telegram", "message", "dialog", "share", "sent_local"]:
        if col not in result.columns:
            result[col] = ""

    if "sent_local" not in result.columns:
        result["sent_local"] = False

    return result.reset_index(drop=True)


def copy_text_button(text: str, key: str) -> None:
    escaped = (
        text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
    )
    st.components.v1.html(
        f"""
        <button onclick="navigator.clipboard.writeText(`{escaped}`)" style="
            width: 100%;
            background: #f0f2f6;
            color: #222;
            border: 1px solid #d0d7de;
            padding: 0.5rem 0.75rem;
            border-radius: 0.5rem;
            cursor: pointer;
            font-size: 14px;
        ">Скопировать текст</button>
        """,
        height=45,
    )


st.set_page_config(page_title="Telegram Outreach", layout="wide")
st.title("Telegram Outreach Helper")
st.caption("Google Sheets → таблица → открыть диалог → отметить отправку")

with st.sidebar:
    st.header("Настройки")
    spreadsheet_url = st.text_input("URL Google Sheets")
    worksheet_name = st.text_input("Название вкладки", value=DEFAULT_WORKSHEET_NAME)
    st.markdown("---")
    st.write("Ожидаемые колонки:")
    st.code("name | phone | telegram | message")
    st.write("Если message пустой, текст сгенерируется автоматически.")

if not spreadsheet_url:
    st.info("Вставь URL Google-таблицы в левую панель.")
    st.stop()

try:
    raw = load_sheet(spreadsheet_url, worksheet_name)
    df = prepare(raw)
except Exception as e:
    import traceback
    st.error(f"Ошибка загрузки таблицы: {type(e).__name__}: {e}")
    st.code(traceback.format_exc())
    st.stop()

if "sent_map" not in st.session_state:
    st.session_state.sent_map = {}

st.metric("Всего строк", len(df))

st.markdown("### Таблица")

if df.empty:
    st.warning("В таблице нет строк.")
else:
    header = st.columns([1.2, 1.0, 1.0, 3.0, 2.1])
    headers = ["name", "phone", "telegram", "message", "actions"]
    for col, title in zip(header, headers):
        col.markdown(f"**{title}**")

    for idx, row in df.iterrows():
        row_key = f"{row['name']}|{row['phone']}|{row['telegram']}|{idx}"
        is_sent = st.session_state.sent_map.get(row_key, False)

        with st.container(border=True):
            cols = st.columns([1.2, 1.0, 1.0, 3.0, 2.1])
            cols[0].write(row["name"] or "—")
            cols[1].write(row["phone"] or "—")
            cols[2].write(row["telegram"] or "—")

            edited_message = cols[3].text_area(
                label=f"message_{idx}",
                value=row["message"],
                height=130,
                label_visibility="collapsed",
                key=f"msg_{idx}"
            )

            dialog = row["dialog"]
            share = share_url(dialog, edited_message)

            with cols[4]:
                if dialog:
                    st.link_button("Открыть диалог", dialog, use_container_width=True)
                else:
                    st.button("Открыть диалог", disabled=True, use_container_width=True, key=f"disabled_open_{idx}")

                if share:
                    copy_text_button(edited_message, key=f"copy_{idx}")
                    st.link_button("Открыть диалог с текстом", share, use_container_width=True)
                else:
                    st.button("Открыть диалог с текстом", disabled=True, use_container_width=True, key=f"disabled_share_{idx}")

                if st.button("Отправлено", key=f"sent_{idx}", use_container_width=True):
                    st.session_state.sent_map[row_key] = not is_sent
                    st.rerun()

                if st.session_state.sent_map.get(row_key, False):
                    st.success("Отправлено")
                else:
                    st.write("")

st.markdown("---")
st.caption("Это версия под хостинг: без локального буфера и без записи статуса в Google Sheets. Отметка ‘Отправлено’ хранится в текущей сессии браузера.")
