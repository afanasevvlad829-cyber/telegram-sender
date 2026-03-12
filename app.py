import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from urllib.parse import quote


TIME_FORMAT = "%Y-%m-%d %H:%M"
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
        "phone",
        "telegram",
        "send_time",
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
        status = normalize(row.get("status")).lower() or "pending"

        rows.append({
            "name": normalize(row.get("name")),
            "phone": normalize(row.get("phone")),
            "telegram": normalize(row.get("telegram")),
            "send_time": normalize(row.get("send_time")),
            "topic": normalize(row.get("topic")),
            "message": message,
            "status": status,
            "dialog": durl,
            "share": share_url(durl, message),
        })

    result = pd.DataFrame(rows)
    for col in ["name", "phone", "telegram", "send_time", "topic", "message", "status", "dialog", "share"]:
        if col not in result.columns:
            result[col] = ""

    result["status"] = result["status"].replace("", "pending").fillna("pending")
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
        source_df["phone"].astype(str).str.strip().fillna("") == str(target_row["phone"]).strip()
    ) & (
        source_df["telegram"].astype(str).str.strip().fillna("") == str(target_row["telegram"]).strip()
    ) & (
        source_df["send_time"].astype(str).str.strip().fillna("") == str(target_row["send_time"]).strip()
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


st.set_page_config(page_title="Telegram Outreach", layout="wide")
st.title("Telegram Outreach Helper")
st.caption("Google Sheets → таблица → открыть диалог → отметить отправку")

with st.sidebar:
    st.header("Настройки")
    spreadsheet_url = st.text_input("URL Google Sheets")
    worksheet_name = st.text_input("Название вкладки", value=DEFAULT_WORKSHEET_NAME)
    status_filter = st.selectbox("Фильтр статуса", ["pending", "done", "all"], index=0)
    st.markdown("---")
    st.write("Ожидаемые колонки:")
    st.code("name | phone | telegram | send_time | topic | message | status")
    st.write("Ключ Google хранится в Streamlit secrets.")

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

if status_filter != "all":
    filtered_df = df[df["status"] == status_filter].reset_index(drop=True)
else:
    filtered_df = df.reset_index(drop=True)

m1, m2, m3 = st.columns(3)
m1.metric("Всего строк", len(filtered_df))
m2.metric("Pending", int((filtered_df["status"] == "pending").sum()) if not filtered_df.empty else 0)
m3.metric("Done", int((filtered_df["status"] == "done").sum()) if not filtered_df.empty else 0)

st.markdown("### Таблица")

if filtered_df.empty:
    st.warning("Нет строк для отображения по выбранному фильтру.")
else:
    header = st.columns([1.1, 1.0, 1.0, 1.0, 1.2, 2.6, 0.8, 2.0])
    headers = ["name", "phone", "telegram", "send_time", "topic", "message", "status", "actions"]
    for col, title in zip(header, headers):
        col.markdown(f"**{title}**")

    for idx, row in filtered_df.iterrows():
        is_done = row["status"] == "done"
        with st.container(border=True):
            cols = st.columns([1.1, 1.0, 1.0, 1.0, 1.2, 2.6, 0.8, 2.0])
            cols[0].write(row["name"] or "—")
            cols[1].write(row["phone"] or "—")
            cols[2].write(row["telegram"] or "—")
            cols[3].write(row["send_time"] or "—")
            cols[4].write(row["topic"] or "—")
            cols[5].text_area(
                label=f"message_{idx}",
                value=row["message"],
                height=120,
                label_visibility="collapsed",
                disabled=True,
                key=f"msg_{idx}"
            )
            if is_done:
                cols[6].success("done")
            else:
                cols[6].warning("pending")

            with cols[7]:
                if row["dialog"]:
                    st.link_button("Открыть диалог", row["dialog"], use_container_width=True)
                else:
                    st.button("Открыть диалог", disabled=True, use_container_width=True, key=f"disabled_open_{idx}")

                if row["share"]:
                    st.link_button("Открыть диалог с текстом", row["share"], use_container_width=True)
                else:
                    st.button("Открыть диалог с текстом", disabled=True, use_container_width=True, key=f"disabled_share_{idx}")

                if is_done:
                    st.success("Отправлено")
                else:
                    if st.button("Отправлено", key=f"done_{idx}", use_container_width=True):
                        try:
                            update_status_in_sheet(spreadsheet_url, worksheet_name, filtered_df, idx, "done")
                            st.success("Статус обновлён")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Не удалось обновить статус: {e}")

st.markdown("---")
st.caption("Для деплоя в Streamlit Cloud: загрузи app.py в GitHub, добавь requirements.txt и внеси service account в Secrets.")
