"""DB 초기 데이터 시드 — admin 계정 + 투자 교육 토픽."""


def _seed_admin_user(cur) -> None:
    """최초 Admin 계정 시드 — 이미 존재하면 스킵"""
    import os
    from api.auth.password import hash_password

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "changeme123")

    cur.execute("SELECT 1 FROM users WHERE email = %s", (admin_email,))
    if cur.fetchone():
        return

    pw_hash = hash_password(admin_password)
    cur.execute(
        "INSERT INTO users (email, password_hash, nickname, role) VALUES (%s, %s, %s, %s)",
        (admin_email, pw_hash, "Admin", "admin"),
    )
    print(f"[DB] 최초 Admin 계정 생성: {admin_email}")
    if admin_password == "changeme123":
        print("[DB] ⚠ 기본 Admin 비밀번호 사용 중 — 프로덕션에서 반드시 변경하세요!")


def _seed_education_topics(cur) -> None:
    """교육 토픽 시드 데이터 삽입 (seeds_education/에서 ALL_TOPICS 가져옴)."""
    from shared.db.migrations.seeds_education import ALL_TOPICS
    for t in ALL_TOPICS:
        cur.execute(
            """INSERT INTO education_topics (category, slug, title, summary, content,
                       examples, difficulty, sort_order)
               VALUES (%(category)s, %(slug)s, %(title)s, %(summary)s, %(content)s,
                       %(examples)s::jsonb, %(difficulty)s, %(sort_order)s)
               ON CONFLICT (slug) DO NOTHING""",
            t,
        )
    print(f"[DB] 교육 토픽 {len(ALL_TOPICS)}건 시드 데이터 삽입")
