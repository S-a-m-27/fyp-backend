from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr


# --- 1. Caretaker Signup ---
class UserSignup(BaseModel):
    firstName: str
    lastName: str
    age: int
    email: EmailStr
    password: str


# --- 2. Caretaker / Patient General Login ---
class UserLogin(BaseModel):
    email: str
    password: str
    userType: str
    caretakerEmail: Optional[EmailStr] = None


# --- 3. Patient Registration (By Caretaker) ---
class PatientSignup(BaseModel):
    name: str
    age: int
    passcode: str
    caretaker_email: EmailStr


# --- 4. QR Login Payload ---
class QRLoginRequest(BaseModel):
    qr_token: str


class CaretakerCreate(BaseModel):
    firstName: str
    lastName: str
    age: int
    email: str
    password: str


# --- 5. Patient Read Schema ---
class PatientSchema(BaseModel):
    id: int
    name: Optional[str] = None
    age: Optional[int] = 0
    relation: Optional[str] = None
    profession: Optional[str] = None
    dob: Optional[str] = None
    location: Optional[str] = None
    login_id: Optional[str] = None
    passcode: Optional[str] = None
    medical_info: Optional[str] = None
    interests: Optional[str] = None  # JSON-encoded list, parsed on the client
    qr_token: Optional[str] = None
    profile_photo_path: Optional[str] = None
    caretaker_email: Optional[str] = None
    memory_training_completed: bool = False

    class Config:
        from_attributes = True


class GenericTopicInfo(BaseModel):
    slug: str
    label: str
    blurb: str
    approx_count: int = 0
    # Default on-disk bundle folder name (e.g. ``included``); each bundle has its own ``manifest.json``.
    default_bundle_slug: str = "included"


class GenericBundleSummary(BaseModel):
    """One purchasable / installable image set under a topic (folder on disk)."""

    topic_slug: str
    bundle_slug: str
    image_count: int


class CatalogBundleDetail(BaseModel):
    """Bundle row for catalog UI (ratings + purchase state)."""

    topic_slug: str
    bundle_slug: str
    display_name: str
    image_count: int
    average_rating: float = 0.0
    rating_count: int = 0
    is_purchased: bool = False
    # True when a purchase row exists but admin has not yet approved (patient has no access).
    purchase_pending_admin: bool = False
    # First image in bundle (for card thumbnails in the app).
    cover_file_path: Optional[str] = None
    # From bundle ``manifest.json`` key ``__bundle__`` (default: free).
    is_free: bool = True
    price_cents: int = 0
    currency: str = "USD"


class BundleRatePayload(BaseModel):
    patient_id: int
    topic_slug: str
    bundle_slug: str
    stars: int
    passcode: Optional[str] = None
    qr_token: Optional[str] = None


class BundleRateResponse(BaseModel):
    status: str
    average_rating: float
    rating_count: int


class BundlePurchasePayload(BaseModel):
    caretaker_email: str
    patient_id: int
    topic_slug: str
    bundle_slug: str


class BundlePurchaseResponse(BaseModel):
    status: str
    already_owned: bool = False
    # When True, patient cannot access bundle until admin approves (see CaretakerBundlePurchase.locked).
    locked: bool = False
    purchase_id: Optional[int] = None


class MemoryGalleryItem(BaseModel):
    id: int
    title: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    file_path: str
    category: str
    library_type: str
    library_topic: Optional[str] = None
    library_collection_slug: Optional[str] = None
    memory_type: Optional[str] = None
    patient_id: Optional[int] = None
    related_person_name: Optional[str] = None
    related_person_relation: Optional[str] = None


class PatientTrainingCompleteRequest(BaseModel):
    patient_id: int
    passcode: Optional[str] = None
    qr_token: Optional[str] = None


class PatientTrainingMemoryDeleteResponse(BaseModel):
    status: str = "ok"
    action: str  # dismissed_library | deleted_personal | removed_shared_access


class PatientTrainingCompleteResponse(BaseModel):
    status: str
    memory_training_completed: bool


class QuizAttemptRecordIn(BaseModel):
    """Patient-submitted summary when they finish a quiz round."""

    quiz_format: str  # caretaker_defined | legacy_pool
    correct_count: int
    wrong_count: int = 0
    target_score: int


class QuizAttemptRecordOut(BaseModel):
    status: str = "ok"
    id: int
    training_sessions_reset: bool = False


class PatientWellnessIntroCompleteRequest(BaseModel):
    patient_id: int
    passcode: Optional[str] = None
    qr_token: Optional[str] = None


class PatientTrainingSessionFinishRequest(BaseModel):
    patient_id: int
    passcode: Optional[str] = None
    qr_token: Optional[str] = None


class PatientImageRatingRequest(BaseModel):
    patient_id: int
    memory_item_id: int
    stars: int  # 1–5
    passcode: Optional[str] = None
    qr_token: Optional[str] = None


class PatientTrainingProgressResponse(BaseModel):
    wellness_intro_completed: bool
    training_sessions_completed: int
    quiz_unlocked: bool
    memory_training_completed: bool


class FaceTrainingResult(BaseModel):
    """Face embedding training outcome (e.g. after personal memory upload)."""

    status: str  # "ok" | "error"
    detail: Optional[str] = None
    images_processed: Optional[int] = None


# --- 6. Memory Item Read Schema ---
class MemoryItemSchema(BaseModel):
    id: int
    patient_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    related_person_name: Optional[str] = None
    related_person_relation: Optional[str] = None
    category: str
    library_type: str
    library_topic: Optional[str] = None
    library_collection_slug: Optional[str] = None
    memory_type: Optional[str] = None
    year: Optional[int] = None
    location: Optional[str] = None
    caretaker_email: Optional[str] = None
    file_path: str
    file_paths: List[str] = []
    created_at: datetime
    shared_with_ids: List[int] = []
    shared_with_names: List[str] = []
    ai_training: Optional[FaceTrainingResult] = None

    class Config:
        from_attributes = True


# --- Memory Sharing Schemas ---
class ShareMemoryRequest(BaseModel):
    patient_ids: List[int]


class ShareablePatient(BaseModel):
    id: int
    name: str
    relation: Optional[str] = None
    profile_photo_path: Optional[str] = None
    has_access: bool = False

    class Config:
        from_attributes = True


# ---------- Dashboard Schemas ----------

class DashboardStats(BaseModel):
    totalPatients: int
    totalMemories: int
    sessionsThisWeek: int
    activeToday: int


class CaretakerProfile(BaseModel):
    userName: str
    userEmail: str
    memberSince: str  # year only e.g. "2026"

    class Config:
        from_attributes = True


class RecentSession(BaseModel):
    id: int
    patientId: Optional[int] = None
    patientName: str
    mode: str
    minutes: int
    startedAt: datetime

    class Config:
        from_attributes = True


class DashboardOverview(BaseModel):
    profile: CaretakerProfile
    stats: DashboardStats
    recentSessions: List[RecentSession]


class SessionCreate(BaseModel):
    patient_id: int
    mode: str
    duration_minutes: int = 0


# ---------- Caretaker quiz pool (subset of patient-visible memories) ----------


class QuizPoolPutRequest(BaseModel):
    memory_ids: List[int]


class QuizPoolStateResponse(BaseModel):
    pool_memory_ids: List[int]
    candidates: List[MemoryItemSchema]


class QuizPoolPutResponse(BaseModel):
    status: str = "ok"
    count: int


# ---------- Caretaker-defined fixed-length quiz ----------


class DefinedQuizQuestionSlot(BaseModel):
    """One slot: legacy (title + 3 wrong) or four-option card (personal quick-add)."""

    slot: int  # 1..10 (see models.DEFINED_QUIZ_QUESTION_SLOTS)
    memory_item_id: int
    wrong_option_1: Optional[str] = None
    wrong_option_2: Optional[str] = None
    wrong_option_3: Optional[str] = None
    four_options: Optional[List[str]] = None
    correct_option_index: Optional[int] = None  # 0..3 when four_options set


class DefinedQuizPutRequest(BaseModel):
    questions: List[DefinedQuizQuestionSlot]


class DefinedQuizSlotState(BaseModel):
    slot: int
    memory_item_id: Optional[int] = None
    correct_title: Optional[str] = None
    file_path: Optional[str] = None
    library_type: Optional[str] = None
    library_topic: Optional[str] = None
    library_collection_slug: Optional[str] = None
    wrong_option_1: Optional[str] = None
    wrong_option_2: Optional[str] = None
    wrong_option_3: Optional[str] = None
    four_options: Optional[List[str]] = None
    correct_option_index: Optional[int] = None


class QuizPersonDefaultsResponse(BaseModel):
    """Reuse the same person name/relation as the patient’s latest personal memory upload."""

    related_person_name: str
    related_person_relation: Optional[str] = None


class DefinedQuizMemoryPick(BaseModel):
    id: int
    title: str
    file_path: str
    library_type: str
    library_topic: Optional[str] = None
    library_collection_slug: Optional[str] = None


class DefinedQuizEditorResponse(BaseModel):
    """Editor payload: purchased-only generic picks + personal picks + current slots."""

    has_quiz: bool
    slots: List[DefinedQuizSlotState]
    generic_purchased_memories: List[DefinedQuizMemoryPick]
    personal_memories: List[DefinedQuizMemoryPick]


class DefinedQuizPutResponse(BaseModel):
    status: str = "ok"
    question_count: int = 10


class QuizPersonalFaceVerifyIn(BaseModel):
    memory_item_id: int
    selected_label: str


class QuizPersonalFaceVerifyOut(BaseModel):
    ok: bool
    predicted_name: Optional[str] = None
    confidence: float = 0.0
    detail: Optional[str] = None


# ---------- Admin (wallet + purchase approval) ----------


class AdminWalletBalanceRow(BaseModel):
    currency: str
    balance_cents: int


class AdminWalletSummary(BaseModel):
    balances: List[AdminWalletBalanceRow]


class AdminLedgerRow(BaseModel):
    id: int
    amount_cents: int
    currency: str
    purchase_id: Optional[int] = None
    description: Optional[str] = None
    created_at: Optional[datetime] = None


class AdminPendingPurchaseItem(BaseModel):
    id: int
    caretaker_email: str
    patient_id: int
    patient_name: Optional[str] = None
    library_topic: str
    library_collection_slug: str
    price_cents: Optional[int] = None
    currency: Optional[str] = None
    locked: bool = True
    purchased_at: Optional[datetime] = None


class AdminNotificationItem(BaseModel):
    id: int
    purchase_id: Optional[int] = None
    message: str
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AdminApprovePurchaseResponse(BaseModel):
    status: str
    already_unlocked: bool = False
