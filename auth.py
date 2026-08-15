# -*- coding: utf-8 -*-
"""
アクセス制限（合言葉ログイン）と、公開時の安全設定。

設計方針
- 事務所PCでのローカル起動（起動.command が環境変数 LOCAL_TRUSTED=1 を立てる）は
  信頼環境とみなし、合言葉なしで使える。
- ネット公開（Streamlit Community Cloud 等。LOCAL_TRUSTED が無い）では、
  合言葉ログインを必須にする（フェイルクローズ）。
- 合言葉はコードやGitには置かず、Streamlit の Secrets（[access] テーブル）に
  「表示名 = 合言葉」で1人1行。行を消せばその人だけ即無効化できる。
- 公開時は依頼者情報を外部に出さないため、案件保存は既定オフ。
  管理者が Secrets で allow_case_save=true にしたときだけ有効。
"""
import os
import hmac
import streamlit as st


def _is_local_trusted() -> bool:
    return os.environ.get("LOCAL_TRUSTED") == "1"


def get_secret(key, default=None):
    """Secrets → 環境変数 → 既定値 の順で値を取る（Secrets未設定でも例外にしない）。"""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


def _access_users() -> dict:
    """Secrets の [access] テーブル（表示名 -> 合言葉）を辞書で返す。無ければ空。"""
    try:
        if "access" in st.secrets:
            return {str(k): str(v) for k, v in dict(st.secrets["access"]).items()}
    except Exception:
        pass
    return {}


def require_login() -> str:
    """
    ログインを要求し、認証済みの利用者名を返す。未認証なら画面を止める。
    ローカル信頼環境では合言葉なしで通す。
    """
    if _is_local_trusted():
        return "事務所PC（ローカル）"
    if st.session_state.get("auth_user"):
        return st.session_state["auth_user"]

    users = _access_users()
    st.title("🔒 ログイン")
    if not users:
        # 公開されているのに合言葉が未設定 → 開けっ放しを防ぐため止める
        st.error("アクセス設定（合言葉）がまだ設定されていません。管理者にご連絡ください。")
        st.caption("管理者向け：アプリの Settings → Secrets に [access] を追加してください"
                   "（デプロイ手順書 参照）。")
        st.stop()

    st.caption("事務所から共有された『合言葉』を入力してください。")
    pw = st.text_input("合言葉", type="password", key="login_pw")
    if st.button("入室する", type="primary"):
        matched = None
        for name, secret in users.items():
            # 日本語の合言葉でも安全に比較できるよう、バイト列にして定数時間比較
            if pw and hmac.compare_digest(str(pw).encode("utf-8"),
                                          str(secret).encode("utf-8")):
                matched = name
                break
        if matched:
            st.session_state["auth_user"] = matched
            st.session_state.pop("login_fail", None)
            st.rerun()
        else:
            st.session_state["login_fail"] = st.session_state.get("login_fail", 0) + 1
    if st.session_state.get("login_fail"):
        st.error(f"合言葉が違います（{st.session_state['login_fail']}回目）。")
    st.caption("※ この画面から先へは、正しい合言葉がないと進めません。")
    st.stop()


def logout_button():
    """サイドバーにログイン状況とログアウトを表示（ローカルでは非表示）。"""
    if _is_local_trusted():
        st.sidebar.caption("🖥 事務所PC（ローカル）で利用中")
        return
    user = st.session_state.get("auth_user")
    if user:
        st.sidebar.caption(f"👤 ログイン中：{user}")
        if st.sidebar.button("ログアウト", use_container_width=True):
            st.session_state.pop("auth_user", None)
            st.rerun()


def case_save_enabled() -> bool:
    """案件の保存・読込を許可するか。ローカルは可。公開時は Secrets で明示許可したときだけ。"""
    if _is_local_trusted():
        return True
    val = get_secret("allow_case_save", False)
    return str(val).lower() in ("1", "true", "yes", "on")


def resolve_api_key(local_fallback: str = "") -> str:
    """APIキーを Secrets（REINFOLIB_API_KEY）優先で取得。無ければローカル保存値。"""
    key = get_secret("REINFOLIB_API_KEY", "") or ""
    return str(key) or local_fallback


def api_key_is_shared() -> bool:
    """APIキーが Secrets 側で共有設定されているか（画面の入力欄を隠す判断に使う）。"""
    return bool(get_secret("REINFOLIB_API_KEY", ""))
