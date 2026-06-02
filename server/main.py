import json
import time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import inspect, text

from fastapi import FastAPI, Depends, HTTPException, Header, Request
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import User, UsageLog, UserSetting
from schemas import (
    SignupRequest,
    LoginRequest,
    CorrectResponse,
    TokenResponse,
    CorrectRequest,
    SummaryRequest,
    SummaryResponse,
    EvaluationRequest,
    EvaluationResponse,
    TitleRequest,
    TitleResponse,
    ToneRequest,
    ToneResponse,
    UsageLogCreateRequest,
    UsageLogResponse,
    UserSettingsRequest,
    UserSettingsResponse,
    AccountVerifyRequest,
    AccountUpdateRequest,
    AccountResponse,
)
from auth import hash_password, verify_password, create_access_token, create_remember_access_token, decode_access_token
from ai_service import AIService

Base.metadata.create_all(bind=engine)


def ensure_usage_log_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("usage_logs")}
    statements = []
    if "feature_type" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN feature_type INTEGER NOT NULL DEFAULT 2")
    if "title" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN title VARCHAR(255)")
    if "score" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN score INTEGER")
    if "tone" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN tone VARCHAR(100)")
    if "spelling_feedback" not in column_names:
        statements.append("ALTER TABLE usage_logs ADD COLUMN spelling_feedback TEXT")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_usage_log_columns()


def ensure_user_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("users")}
    statements = []
    if "display_name" not in column_names:
        statements.append("ALTER TABLE users ADD COLUMN display_name VARCHAR(100)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_user_columns()


def ensure_user_setting_columns():
    inspector = inspect(engine)
    column_names = {column["name"] for column in inspector.get_columns("user_settings")}
    statements = []
    if "spell_scope" not in column_names:
        statements.append("ALTER TABLE user_settings ADD COLUMN spell_scope VARCHAR(30) NOT NULL DEFAULT 'current_sentence'")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


ensure_user_setting_columns()

SERVER_BUILD_ID = "2026-05-29-spell-scope"

app = FastAPI(title="Writing Assistant API")
ai_service = AIService()

_LOG_DIR = Path(__file__).resolve().parents[1] / ".logs"
_SERVER_REQUEST_LOG = _LOG_DIR / "server_requests.jsonl"


def write_server_event(event: str, **fields):
    payload = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "event": event,
        **fields,
    }
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _SERVER_REQUEST_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


@app.middleware("http")
async def request_diagnostics(request: Request, call_next):
    started_at = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        write_server_event(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=int((time.monotonic() - started_at) * 1000),
            client=request.client.host if request.client else "",
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db)
) -> User:
    if not authorization.startswith("Bearer "):#
        raise HTTPException(status_code=401, detail="인증 토큰이 없습니다.")

    token = authorization.replace("Bearer ", "").strip()
    payload = decode_access_token(token)

    if not payload:
        raise HTTPException(status_code=401, detail="유효하지 않은 토큰입니다.")

    expire_at = payload.get("exp")
    if expire_at is not None:
        expire_dt = datetime.fromtimestamp(float(expire_at), tz=timezone.utc).astimezone()
        print(
            f"[auth] token expires at {expire_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="토큰 정보가 올바르지 않습니다.")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")

    return user


@app.get("/")
def root():
    return {"message": "server is running", "build_id": SERVER_BUILD_ID}


@app.post("/signup")
def signup(data: SignupRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.username == data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="이미 존재하는 사용자입니다.")

    user = User(
        username=data.username,
        password_hash=hash_password(data.password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "회원가입 완료"}


@app.post("/login", response_model=TokenResponse)#
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if not user:
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

    if not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 잘못되었습니다.")

    token_factory = create_remember_access_token if data.remember_me else create_access_token
    token = token_factory({"sub": user.username})
    return TokenResponse(access_token=token)


@app.post("/account/verify")
def verify_account(
    data: AccountVerifyRequest,
    current_user: User = Depends(get_current_user),
):
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="비밀번호가 잘못되었습니다.")
    return {"verified": True}


@app.get("/account", response_model=AccountResponse)
def get_account(current_user: User = Depends(get_current_user)):
    return AccountResponse(
        username=current_user.username,
        display_name=current_user.display_name,
    )


@app.put("/account", response_model=AccountResponse)
def update_account(
    data: AccountUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_username = (data.username or current_user.username).strip()
    access_token = None

    if not new_username:
        raise HTTPException(status_code=400, detail="아이디를 입력해 주세요.")

    if new_username != current_user.username:
        existing_user = db.query(User).filter(User.username == new_username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="이미 존재하는 아이디입니다.")
        current_user.username = new_username
        access_token = create_access_token({"sub": current_user.username})

    if data.display_name is not None:
        current_user.display_name = data.display_name.strip() or None
    if data.password:
        if len(data.password) < 4:
            raise HTTPException(status_code=400, detail="비밀번호는 4자 이상으로 입력해 주세요.")
        current_user.password_hash = hash_password(data.password)

    db.commit()
    db.refresh(current_user)
    return AccountResponse(
        username=current_user.username,
        display_name=current_user.display_name,
        access_token=access_token,
    )


@app.delete("/account")
def delete_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(UsageLog).filter(UsageLog.user_id == current_user.id).delete()
    db.query(UserSetting).filter(UserSetting.user_id == current_user.id).delete()
    db.delete(current_user)
    db.commit()
    return {"message": "계정이 삭제되었습니다."}


@app.post("/correct", response_model=CorrectResponse)
def correct_text(
    data: CorrectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="교정할 텍스트가 비어 있습니다.")

    result = run_text_correction(data.text)
    corrected = result["corrected_text"]

    log = UsageLog(
        user_id=current_user.id,
        input_text=data.text,
        output_text=corrected,
        feature_type=2,
        spelling_feedback=result.get("feedback"),
    )
    db.add(log)
    db.commit()

    return CorrectResponse(
        corrected_text=corrected,
        spelling_feedback=result.get("feedback"),
        corrections=result.get("corrections") or [],
    )


@app.post("/correct-public", response_model=CorrectResponse)
def correct_text_public(data: CorrectRequest):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="Text is required.")

    result = run_text_correction(data.text)
    return CorrectResponse(
        corrected_text=result["corrected_text"],
        spelling_feedback=result.get("feedback"),
        corrections=result.get("corrections") or [],
    )


@app.post("/summary-public", response_model=SummaryResponse)
def summarize_text_public(data: SummaryRequest):
    result = run_ai_feature("summary", lambda: ai_service.summarize_text(data.text))
    return SummaryResponse(summary=result["summary"])


@app.post("/evaluation-public", response_model=EvaluationResponse)
def evaluate_text_public(data: EvaluationRequest):
    result = run_ai_feature("evaluation", lambda: ai_service.evaluate_text(data.text))
    return EvaluationResponse(
        score=int(result["score"]),
        feedback=str(result.get("feedback") or ""),
    )


@app.post("/title-public", response_model=TitleResponse)
def recommend_title_public(data: TitleRequest):
    result = run_ai_feature("title", lambda: ai_service.recommend_title(data.text))
    return TitleResponse(title=result["title"])


@app.post("/tone-public", response_model=ToneResponse)
def convert_tone_public(data: ToneRequest):
    result = run_ai_feature("tone", lambda: ai_service.convert_tone(data.text, data.tone))
    return ToneResponse(
        converted_text=result["converted_text"],
        feedback=result.get("feedback"),
    )


def run_text_correction(text_value: str) -> dict[str, str]:
    try:
        return ai_service.correct_text(text_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI correction failed: {exc}") from exc


def run_ai_feature(feature_name: str, callback):
    try:
        return callback()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI {feature_name} failed: {exc}") from exc


@app.post("/logs", response_model=UsageLogResponse)
def create_usage_log(
    data: UsageLogCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.feature_type not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Unsupported feature_type.")
    if not data.input_text.strip():
        raise HTTPException(status_code=400, detail="input_text is required.")
    if data.score is not None and not 0 <= int(data.score) <= 100:
        raise HTTPException(status_code=400, detail="score must be between 0 and 100.")

    if data.feature_type == 1:
        existing_log = (
            db.query(UsageLog)
            .filter(
                UsageLog.user_id == current_user.id,
                UsageLog.feature_type == 1,
                UsageLog.input_text == data.input_text,
            )
            .order_by(UsageLog.created_at.desc())
            .first()
        )
        if existing_log:
            if data.title is not None:
                existing_log.title = data.title
            if data.score is not None:
                existing_log.score = data.score
            existing_log.output_text = data.output_text or existing_log.output_text or ""
            db.commit()
            db.refresh(existing_log)
            return existing_log

    log = UsageLog(
        user_id=current_user.id,
        input_text=data.input_text,
        output_text=data.output_text or "",
        feature_type=data.feature_type,
        title=data.title,
        score=data.score,
        tone=data.tone,
        spelling_feedback=data.spelling_feedback,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


@app.get("/logs", response_model=list[UsageLogResponse])
def list_usage_logs(
    feature_type: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(UsageLog).filter(UsageLog.user_id == current_user.id)
    if feature_type is not None:
        query = query.filter(UsageLog.feature_type == feature_type)
    return query.order_by(UsageLog.created_at.desc()).all()


@app.get("/settings", response_model=UserSettingsResponse)
def get_user_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        return UserSettingsResponse(has_settings=False)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        spell_scope=settings.spell_scope,
        updated_at=settings.updated_at,
    )


@app.put("/settings", response_model=UserSettingsResponse)
def update_user_settings(
    data: UserSettingsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    input_mode = "clipboard" if data.input_mode == "clipboard" else "realtime"
    replace_mode = bool(data.replace_mode) and input_mode == "realtime"
    spell_scope = data.spell_scope if data.spell_scope in {"current_sentence", "current_paragraph", "full_text"} else "current_sentence"
    settings = db.query(UserSetting).filter(UserSetting.user_id == current_user.id).first()
    if not settings:
        settings = UserSetting(user_id=current_user.id)
        db.add(settings)

    settings.default_dark_mode = bool(data.default_dark_mode)
    settings.history_enabled = bool(data.history_enabled)
    settings.input_mode = input_mode
    settings.replace_mode = replace_mode
    settings.spell_scope = spell_scope
    settings.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(settings)
    return UserSettingsResponse(
        has_settings=True,
        default_dark_mode=settings.default_dark_mode,
        history_enabled=settings.history_enabled,
        input_mode=settings.input_mode,
        replace_mode=settings.replace_mode,
        spell_scope=settings.spell_scope,
        updated_at=settings.updated_at,
    )
