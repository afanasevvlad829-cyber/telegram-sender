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


def get_worksheet(spreadsheet_url: str, worksheet_name: str):
    client = get_gspread_client()
    sheet = client.open_by_url(spreadsheet_url)
    return sheet.worksheet(worksheet_name)


def ensure_required_columns(df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "name",
        "Age",
        "phone",
        "telegram",
        "topic",
        "message",
        "status",
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
        status = normalize(row.get("status")).lower() or ""

        rows.append({
            "name": normalize(row.get("name")),
            "age": normalize(row.get("Age")),
            "phone": normalize(row.get("phone")),
            "telegram": normalize(row.get("telegram")),
            "topic": normalize(row.get("topic")),
            "message": message,
            "status": status,
            "dialog": durl,
            "share": share_url(durl, message),
        })

    result = pd.DataFrame(rows)
    for col in ["name", "age", "phone", "telegram", "topic", "message", "status", "dialog", "share"]:
        if col not in result.columns:
            result[col] = ""

    return result.reset_index(drop=True)


def update_status_in_sheet(spreadsheet_url: str, worksheet_name: str, filtered_df: pd.DataFrame, filtered_row_index: int, new_status: str) -> None:
    ws = get_worksheet(spreadsheet_url, worksheet_name)
    headers = ws.row_values(1)

    if not headers:
        raise RuntimeError("В листе нет заголовков первой строки.")

    all_records = ws.get_all_records()
    source_df = pd.DataFrame(all_records)
    source_df = ensure_required_columns(source_df)

    target_row = filtered_df.iloc[filtered_row_index]

    match_mask = (
        source_df["name"].astype(str).str.strip().fillna("") == str(target_row["name"]).strip()
    ) & (
        source_df["Age"].astype(str).str.strip().fillna("") == str(target_row["age"]).strip()
    ) & (
        source_df["phone"].astype(str).str.strip().fillna("") == str(target_row["phone"]).strip()
    ) & (
        source_df["telegram"].astype(str).str.strip().fillna("") == str(target_row["telegram"]).strip()
    ) & (
        source_df["topic"].astype(str).str.strip().fillna("") == str(target_row["topic"]).strip()
    )

    matches = source_df[match_mask]

    if matches.empty:
        raise RuntimeError("Не удалось найти строку в Google Sheets для обновления статуса.")

    source_index = matches.index[0]

    try:
        status_col = headers.index("status") + 1
    except ValueError:
        status_col = len(headers) + 1
        ws.update_cell(1, status_col, "status")

    ws.update_cell(source_index + 2, status_col, new_status)
    load_sheet.clear()


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


def open_with_text_button(text: str, url: str, key: str) -> None:
    if not url:
        st.button("Открыть диалог с текстом", disabled=True, use_container_width=True, key=key)
        return

    escaped_text = (
        text.replace("\\", "\\\\")
            .replace("`", "\\`")
            .replace("$", "\\$")
    )
    escaped_url = url.replace("&", "&amp;").replace('"', '&quot;')

    st.components.v1.html(
        f"""
        <button onclick="navigator.clipboard.writeText(`{escaped_text}`).then(function() {{ window.open('{escaped_url}', '_blank'); }}).catch(function() {{ window.open('{escaped_url}', '_blank'); }});" style="
            width: 100%;
            background: #ffffff;
            color: #222;
            border: 1px solid #d0d7de;
            padding: 0.5rem 0.75rem;
            border-radius: 0.5rem;
            cursor: pointer;
            font-size: 14px;
        ">Открыть диалог с текстом</button>
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
    st.code("name | Age | phone | telegram | message")
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

st.metric("Всего строк", len(df))

st.markdown("### Таблица")

if df.empty:
    st.warning("В таблице нет строк.")
else:
    header = st.columns([1.1, 0.7, 1.0, 1.0, 3.0, 2.1])
    headers = ["name", "age", "phone", "telegram", "message", "actions"]
    for col, title in zip(header, headers):
        col.markdown(f"**{title}**")

    for idx, row in df.iterrows():
        is_sent = normalize(row.get("status")).lower() == "done"

        with st.container(border=True):
            cols = st.columns([1.1, 0.7, 1.0, 1.0, 3.0, 2.1])
            cols[0].write(row["name"] or "—")
            cols[1].write(row["age"] or "—")
            cols[2].write(row["phone"] or "—")
            cols[3].write(row["telegram"] or "—")

            edited_message = cols[4].text_area(
                label=f"message_{idx}",
                value=row["message"],
                height=130,
                label_visibility="collapsed",
                key=f"msg_{idx}"
            )

            dialog = row["dialog"]
            share = share_url(dialog, edited_message)

            with cols[5]:
                if dialog:
                    st.link_button("Открыть диалог", dialog, use_container_width=True)
                else:
                    st.button("Открыть диалог", disabled=True, use_container_width=True, key=f"disabled_open_{idx}")

                copy_text_button(edited_message, key=f"copy_{idx}")
                open_with_text_button(edited_message, share, key=f"share_{idx}")

                button_label = "Снять отметку" if is_sent else "Отправлено"
                if st.button(button_label, key=f"sent_{idx}", use_container_width=True):
                    try:
                        new_status = "" if is_sent else "done"
                        update_status_in_sheet(spreadsheet_url, worksheet_name, df, idx, new_status)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Не удалось обновить статус: {e}")

                if normalize(row.get("status")).lower() == "done":
                    st.success("Отправлено")
                else:
                    st.write("")

st.markdown("---")
st.caption("Отметка ‘Отправлено’ теперь пишется в Google Sheets в скрытую рабочую колонку status, даже если ты не показываешь её в интерфейсе.")
